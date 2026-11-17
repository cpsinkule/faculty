// Sending grades to Gradescope (AGENTS.md "Sending grades to Gradescope"; requirement, 2026-09-25). A panel on the grading Certification and Export step. The server decides everything: the preview says what
// would be uploaded and set and why anything would not, the send names that preview's fingerprint, and the background sender
// uploads and grades one at a time, reading each back. This page only shows it, asks, and never retries a send by itself.
// The same panel sends bubble sheets from the Bubble sheets page (kind "sheets"; requirement, 2026-09-25): each sheet
// Faculty matched to a student goes to the version its bubbles name, and Gradescope scores it; no grade is sent.
// An exam whose grades are already published is a warning, not a refusal (requirement, 2026-09-26): Send asks once more in a popup, and the send names each published exam confirmed, which the server checks.
// When a section's Gradescope course has no assignment for this yet, Faculty can make it (requirement, 2026-09-27; any assignment, not
// only exams): `grading-gradescope-create.js` holds that dialog and the progress it shows here.
// Papers may go without their grades (requirement, 2026-09-28): the send dialog's "Papers only" choice, also opened by "Upload papers only…".
// The server plans no grade and reads no rubric for it; the choice is part of the preview's fingerprint and of the send's record.
// Grades someone else made on Gradescope (a TA's, or the instructor's own there) are left alone unless the instructor ticks
// "Replace grades someone else made on Gradescope" for a send (requirement, 2026-10-01): the
// preview then lists each grade that differs from Faculty's, Send asks once more in a popup, and the send names how many it
// replaces, which the server checks.
import {runFormAction, tableMarkup, whenVisible} from "./ui.js";
import {createDialog, creationLive, creationsMarkup} from "./grading-gradescope-create.js";
import {confirmPopup} from "./grading-gradescope-publish.js";
import {patchKeepingDisclosures} from "./grading-jobs.js";

// ONE PLACE TO PUBLISH (2026-10-18 review item 10): Publish grades on Gradescope… is Certify's header action only; a notice that a
// grade is not published yet says where it is, as words, never a second "Publish grades…" button. Plain text (no markup characters).
export const PUBLISH_WHERE = "Use Publish grades on Gradescope… at the top of Certify";

// What differs between sending a written exam and sending bubble sheets: the route and the words for it.
export const KINDS = {
  written: {path: "", title: "Send grades to Gradescope", button: "Send to Gradescope…", dialog: "Send to Gradescope",
    lead: "Uploads each student's paper to the Gradescope assignment of their version and ticks the rubric items of their final grade.",
    rules: "You see a preview of what would be sent before anything is. Only final grades go; nothing is published or emailed, the only comment is a grade's lines added on the certification page, and Faculty never touches a paper someone else put on Gradescope, nor a grade someone else made there unless you choose to replace those grades for a send.",
    route: "Each section is sent to its own Gradescope course; each paper goes to the assignment of its version.",
    confirm: "I have checked the preview. Faculty uploads these papers and sets these grades on Gradescope; nothing is published.",
    sent: "Send approved. Faculty uploads the papers and sets the grades in the background; follow it on Certify.",
    papersConfirm: "I have checked the preview. Faculty uploads these papers to Gradescope and sets no grade; nothing is published.",
    papersSent: "Upload approved. Faculty uploads the papers in the background and sends no grades; follow it on Certify.",
    proposed: "Proposed by Faculty from the course name and the assignment’s title and versions. Check it before sending.",
    nothing: "Nothing to send: every final grade here is already on Gradescope, held or refused, or no section has its Gradescope assignment chosen (or every section is left out)."},
  sheets: {path: "/sheets", title: "Send bubble sheets to Gradescope", button: "Send bubble sheets to Gradescope…", dialog: "Send bubble sheets to Gradescope",
    lead: "Upload each bubble sheet Faculty matched to a student to the section's Gradescope bubble-sheet exam, as that student, in the version the sheet's bubbles name.",
    rules: "Upload only: Gradescope reads the bubbles and scores them with its own answer key, so no grade is sent. Only sheets of scans that finished their scan checks go, nothing is published or emailed, and Faculty never replaces a sheet someone else put on Gradescope.",
    route: "Each section is sent to its own Gradescope course; each sheet goes to the version its bubbles name (or its review records).",
    confirm: "I have checked the preview. Faculty uploads these bubble sheets to Gradescope; Gradescope scores them, and nothing is published.",
    sent: "Send approved. Faculty uploads the sheets in the background; follow it on this page.",
    proposed: "Proposed by Faculty from the course name and the exam’s title. Check it before sending.",
    nothing: "Nothing to send: every matched sheet is already on Gradescope or skipped, or no section has its Gradescope bubble-sheet exam chosen (or every section is left out)."},
};

// A preview and a send read Gradescope (rosters, outlines, rubrics, submissions of every section): far longer than 15 seconds.
const WAIT = 180000;
const POLL = 4000;
// The course coordinator's course (requirement, 2026-09-25): never listed, even if a server bug sent it.
const FORBIDDEN = ["math 140 for instructors"];

// Plain words for what happened to a paper or a grade.
export const unitWords = {accepted: "on Gradescope", present: "left alone (already on Gradescope)", refused: "not sent", failed: "did not go through",
  uncertain: "checking", pending: "waiting", sending: "sending", cancelled: "cancelled"};
const unitOrder = ["accepted", "present", "refused", "failed", "uncertain", "sending", "pending", "cancelled"];
const openStates = ["pending", "sending", "uncertain"];
export const sendWords = {pending: "Waiting to start", running: "Sending", complete: "Finished", cancelled: "Cancelled"};
const live = send => ["pending", "running"].includes(send.status);
const quiet = ["accepted", "pending", "sending", "cancelled"];

// A send whose answer never arrived, kept across closing and reopening the dialog: only an explicit Retry request sends the SAME
// payload again, and the server answers a repeated request ID with the send it already recorded (never a second send).
const unresolved = new Map();

const normalized = text => String(text || "").split(/\s+/).filter(Boolean).join(" ").toLowerCase();
export const forbidden = course => FORBIDDEN.includes(normalized(course.short)) || FORBIDDEN.includes(normalized(course.name));
const count = (n, one, many = `${one}s`) => `${Number(n || 0).toLocaleString()} ${Number(n) === 1 ? one : many}`;
const number = n => Number(n || 0).toLocaleString();
const sum = values => Object.values(values || {}).reduce((total, n) => total + Number(n || 0), 0);
const base = (plan, kind = "written") => `/api/grading/gradescope/assignments/${encodeURIComponent(plan.id)}${KINDS[kind].path}`;

// "Question 3 · Version B" from the plan's own questions; the server sends question keys, never students' UINs.
function questionTitle(plan, key, version = null) {
  if (!key) return "";
  const question = (plan.questions || []).find(q => q.question_key === key && (version === null || (q.exam_version || "") === (version || "")));
  const letter = version ?? question?.exam_version ?? "";
  return `${question?.content?.title || key}${letter ? ` · Version ${letter}` : ""}`;
}

// What an approved preview would write: papers (or sheets) to upload and grades to send or update.
export function sendable(preview) {
  const s = preview?.summary || {};
  return Number(s.papers?.upload || 0) + Number(s.grades?.send || 0) + Number(s.grades?.update || 0) + Number(s.grades?.replace || 0) + Number(s.sheets?.upload || 0);
}

// --- the panel --------------------------------------------------------------------------------------------------------------
// Resolves to {refresh()} once the first status is read: the page asks it to read again after a send it started elsewhere (the
// Certify dialog's hand-off; certify-2).
export async function mountGradescope(host, plan, h, active, kind = "written", {onStatus = null} = {}) {
  if (!host || !plan) return;
  let timer = null, updating = false, again = false, status = null, written = "";  // `written`: the markup the panel holds (QUIET REFRESH)
  const shown = new WeakMap();  // what each problems list holds
  const words = KINDS[kind], title = `gradescope-title-${kind}`;
  const running = () => active() && host.isConnected;
  host.innerHTML = `<section class="grading-panel grading-gradescope" data-gradescope-kind="${h.e(kind)}" aria-labelledby="${title}"><h3 id="${title}">${h.e(words.title)}</h3><p class="loading-state" role="status">Checking the Gradescope connection…</p></section>`;

  async function update() {
    if (updating) { again = true; return; }
    updating = true;
    clearTimeout(timer);
    try {
      try {
        status = await h.api(base(plan, kind));
      } catch (error) {
        written = "";
        if (running()) host.querySelector(".grading-gradescope").innerHTML = `<h3 id="${title}">${h.e(words.title)}</h3><p role="alert">Faculty could not load what was sent to Gradescope: ${h.e(error.message)}</p><div class="grading-actions">${h.button("gradescope-reload", "Try again")}</div>`;
        host.querySelector('[data-grading-action="gradescope-reload"]')?.addEventListener("click", () => { void update(); });
        return;
      }
      if (!running()) return;
      draw();
      onStatus?.(status);  // the page's own summary (Bubble sheets) says what is on Gradescope from the same read
    } finally {
      updating = false;
      // A refresh asked for meanwhile runs at once; the poll of a live send waits while the tab is hidden and reads once it is shown.
      if (running() && again) timer = setTimeout(() => { if (running()) void update(); }, 0);
      else if (running() && ((status?.sends || []).some(live) || (status?.creations || []).some(creationLive) || status?.mc_check?.running || status?.mc_check?.labels?.running || (status?.mc_check?.corrections || []).some(c => ["pending", "sending", "uncertain"].includes(c.state)))) timer = setTimeout(() => whenVisible(() => { if (running()) void update(); }), POLL);
      again = false;
    }
  }

  // A poll that reads the same status changes nothing; a changed one patches the panel in place, keeping the folds the instructor
  // opened, the focus and each problems list (FRONTEND.md "Quiet refresh contract"; certify-3).
  function draw() {
    const panel = host.querySelector(".grading-gradescope");
    const markup = panelMarkup(status, plan, h, kind);
    if (markup !== written) {
      written = markup;
      patchKeepingDisclosures(panel, markup, node => node.dataset.gradescopeFold || node.dataset.gradescopeProblems || (node.hasAttribute("data-gradescope-earlier") ? "earlier" : ""));
      const start = panel.querySelector('[data-grading-action="gradescope-send"]');
      if (start && !start.dataset.secondary) start.classList.add("primary");
    }
    // A list the instructor opened is read again while its send moves.
    panel.querySelectorAll("[data-gradescope-problems]").forEach(details => {
      if (details.open && details.querySelector("[data-gradescope-problem-list]").dataset.loaded && live((status.sends || []).find(item => item.id === details.dataset.gradescopeProblems) || {})) void loadProblems(details);
    });
  }

  // Delegated, so a patch keeps them: the panel's elements are kept while their markup is the same.
  const panel = host.querySelector(".grading-gradescope");
  panel.addEventListener("click", event => {
    const button = /** @type {HTMLElement | null} */ (event.target instanceof Element ? event.target.closest("[data-grading-action]") : null);
    if (!button || !panel.contains(button) || /** @type {HTMLButtonElement} */ (button).disabled) return;
    const action = button.dataset.gradingAction, refresh = () => { if (running()) void update(); };
    const send = button.closest("[data-gradescope-send]")?.dataset.gradescopeSend, creation = button.closest("[data-gradescope-creation]")?.dataset.gradescopeCreation;
    if (action === "gradescope-send") sendDialog(plan, h, refresh, kind);
    else if (action === "gradescope-papers") sendDialog(plan, h, refresh, kind, {papersOnly: true});
    else if (action === "gradescope-create") createDialog(plan, h, refresh);
    // THE MC CHECK: the sender reads Gradescope's bubble-sheet scores a moment later; the panel follows it like a send.
    else if (action === "gradescope-mc-check") void h.run(button, async () => {
      await h.api(`${base(plan)}/mc-check`, "POST", {});
      if (running()) await update();
    });
    // MC LABELS: the sender renames each answer's rubric item on Gradescope a moment later; the panel follows it like a send.
    else if (action === "gradescope-mc-labels") void h.run(button, async () => {
      await h.api(`${base(plan)}/mc-labels`, "POST", {});
      if (running()) await update();
    });
    else if (action === "gradescope-cancel" && send) void h.run(button, async () => {
      if (!window.confirm(`Stop this send? ${kind === "sheets" ? "Sheets" : "Papers and grades"} still waiting are not sent. What is already on Gradescope stays there. To send the rest later, preview and send again.`)) return;
      await h.api(`${base(plan, kind)}/sends/${encodeURIComponent(send)}/cancel`, "POST", {});
      h.toast("Send stopped. Nothing still waiting goes to Gradescope.");
      if (running()) await update();
    });
    else if (action === "gradescope-retry" && send) void h.run(button, async () => {
      await h.api(`${base(plan, kind)}/sends/${encodeURIComponent(send)}/retry`, "POST", {});
      h.toast("Sending again only what did not go through.");
      if (running()) await update();
    });
    else if (action === "gradescope-create-cancel" && creation) void h.run(button, async () => {
      if (!window.confirm("Stop making this on Gradescope? What is already there stays; nothing more is made. To finish it later, create again.")) return;
      await h.api(`${base(plan)}/creations/${encodeURIComponent(creation)}/cancel`, "POST", {});
      h.toast("Stopped. Nothing more is made on Gradescope.");
      if (running()) await update();
    });
    else if (action === "gradescope-create-retry" && creation) void h.run(button, async () => {
      await h.api(`${base(plan)}/creations/${encodeURIComponent(creation)}/retry`, "POST", {});
      h.toast("Trying again where it stopped.");
      if (running()) await update();
    });
  });
  // "Show problems" reads its list when first opened (toggle does not bubble: caught on the way down).
  panel.addEventListener("toggle", event => {
    const details = /** @type {HTMLDetailsElement | null} */ (event.target instanceof Element && event.target.matches("[data-gradescope-problems]") ? event.target : null);
    if (details?.open && !details.querySelector("[data-gradescope-problem-list]").dataset.loaded) void loadProblems(details);
  }, true);

  // A list is written only when it differs, so a read that finds the same problems leaves the table's scroll, selection and focus.
  const put = (slot, html) => { if (shown.get(slot) !== html) { slot.innerHTML = html; shown.set(slot, html); } };
  async function loadProblems(details) {
    const slot = details.querySelector("[data-gradescope-problem-list]");
    if (!slot.dataset.loaded) put(slot, '<p class="loading-state" role="status">Loading what did not go through…</p>');
    try {
      const data = await h.api(`${base(plan, kind)}/sends/${encodeURIComponent(details.dataset.gradescopeProblems)}/problems`);
      if (!running() || !details.isConnected) return;
      if (!slot.dataset.loaded) slot.dataset.loaded = "true";
      put(slot, problemsMarkup(data.problems || [], plan, h, kind));
    } catch (error) {
      if (running() && details.isConnected) put(slot, `<p role="alert">${h.e(error.message)}</p>`);
    }
  }

  await update();
  return {refresh: () => { if (running()) void update(); }};
}

// What is on Gradescope now across EVERY send (the server's `coverage`, counted from Faculty's receipts; nothing is contacted), and
// how much is left. The latest send's card says only what that one send did (2026-09-26: a 10-paper follow-up send read as if only
// 10 papers had gone).
export function remaining(coverage, kind = "written") {
  if (!coverage) return null;
  if (kind === "sheets") {
    const sheets = coverage.sheets || {};
    return Math.max(0, Number(sheets.matched || 0) - Number(sheets.sent || 0));
  }
  const papers = coverage.papers || {}, grades = coverage.grades || {};
  return Math.max(0, Number(papers.total || 0) - Number(papers.sent || 0)) + Number(grades.not_sent || 0) + Number(grades.changed || 0);
}

export function coverageMarkup(coverage, h, kind = "written") {
  if (!coverage) return "";
  const left = remaining(coverage, kind);
  if (kind === "sheets") {
    const sheets = coverage.sheets || {};
    const waiting = Number(sheets.waiting || 0) ? ` · ${count(sheets.waiting, "more sheet")} in scans still waiting for scan checks` : "";
    if (!Number(sheets.matched || 0)) return `<p class="grading-gradescope-coverage" data-gradescope-coverage>No sheet is matched to a student yet${h.e(waiting)}.</p>`;
    return `<p class="grading-gradescope-coverage" data-gradescope-coverage><strong>${h.e(number(sheets.sent))} of ${h.e(count(sheets.matched, "matched sheet"))}</strong> ${Number(sheets.sent) === 1 ? "is" : "are"} on Gradescope${left ? ` · <span class="chip" data-tone="warning">${h.e(number(left))} not sent yet</span>` : ""}${h.e(waiting)}</p>`;
  }
  const papers = coverage.papers || {}, grades = coverage.grades || {};
  if (!Number(papers.total || 0) && !Number(grades.final || 0)) return '<p class="grading-gradescope-coverage" data-gradescope-coverage>No paper has a grade in Faculty yet.</p>';
  const notes = [Number(grades.not_sent || 0) ? `${number(grades.not_sent)} final ${Number(grades.not_sent) === 1 ? "grade" : "grades"} not sent yet` : "",
    Number(grades.changed || 0) ? `${number(grades.changed)} changed since ${Number(grades.changed) === 1 ? "it was" : "they were"} sent` : "",
    Number(papers.total || 0) > Number(papers.sent || 0) ? `${number(Number(papers.total) - Number(papers.sent || 0))} ${Number(papers.total) - Number(papers.sent || 0) === 1 ? "paper" : "papers"} not uploaded` : ""].filter(Boolean);
  return `<p class="grading-gradescope-coverage" data-gradescope-coverage><strong>${h.e(number(papers.sent))} of ${h.e(count(papers.total, "paper"))} · ${h.e(number(grades.sent))} of ${h.e(count(grades.final, "final grade"))}</strong> ${Number(grades.sent) === 1 && Number(papers.sent) <= 1 ? "is" : "are"} on Gradescope${notes.length ? ` ${notes.map(note => `<span class="chip" data-tone="warning">${h.e(note)}</span>`).join(" ")}` : left === 0 ? ' <span class="chip" data-tone="success">everything final is there</span>' : ""}</p>`;
}

// THE MC CHECK is about the bubble sheets: where they have their own panel beside this one (Certify), it is shown there alone
// (requirement, 2026-10-27).
export function panelMarkup(status, plan, h, kind = "written") {
  const words = KINDS[kind];
  const configured = Boolean(status.configured);
  const linked = (status.targets || []).filter(t => t.course_id);
  const sends = status.sends || [];
  const busy = sends.some(live);
  const setup = configured ? "" : `<div class="callout grading-warning" data-tone="warning" role="status" data-gradescope-setup><p><strong>Gradescope is not set up for this Faculty.</strong>${status.problem && !/^Gradescope is not set up/i.test(status.problem) ? ` ${h.e(status.problem)}` : ""}</p><details class="disclosure"><summary><span>How to set it up</span></summary><div class="disclosure-body"><p>Run <code>python3 ~/.config/faculty/save-gradescope.py</code> in a terminal. It asks for your Gradescope email and password, checks them, and saves them to a file only your account can read. The Faculty service must name that file (its <code>gradescope.conf</code> drop-in sets <code>FACULTY_GRADESCOPE_CREDENTIALS_FILE</code>) and be restarted before it can send anything.</p></div></details></div>`;
  const links = linked.length ? `<details class="disclosure grading-batch-disclosure" data-gradescope-fold="links"><summary>Linked on Gradescope · ${h.e(count(linked.length, "section"))}</summary><ul class="grading-gradescope-links">${linked.map(t => `<li><strong>Section ${h.e(t.section)}</strong> <span>→ ${h.e(t.course_title || "course")}${t.container_title ? ` · ${h.e(t.container_title)}` : " · no assignment chosen yet"}</span></li>`).join("")}</ul></details>` : "";
  const latest = sends.slice(0, 1), earlier = sends.slice(1);
  // The send button is the panel's primary action only while something is left to send (or nothing is known yet); a preview
  // can still be looked at when everything is there.
  const left = remaining(status.coverage, kind);
  // Offered while some section has no Gradescope assignment linked for this (the dialog says which, after reading Gradescope).
  const creatable = kind === "written" && !(status.creations || []).some(creationLive) && (!linked.length || (status.targets || []).some(t => !t.container_id));
  return `<h3 id="gradescope-title-${h.e(kind)}">${h.e(words.title)}</h3>
    ${configured ? coverageMarkup(status.coverage, h, kind) : ""}
    <p class="section-lead">${h.e(words.lead)}</p>
    <details class="disclosure grading-gradescope-rules" data-gradescope-fold="rules"><summary><span>How sending works</span></summary><div class="disclosure-body"><p class="supporting-copy">${h.e(words.rules)}</p></div></details>
    ${setup}${configured ? `<div class="grading-actions">${h.button("gradescope-send", words.button, `${left === 0 ? 'data-secondary="true" ' : ""}${busy ? `aria-describedby="gradescope-busy-${h.e(kind)}"` : ""}`)}${kind === "written" ? h.button("gradescope-papers", "Upload papers only…") : ""}${creatable ? h.button("gradescope-create", "Create the assignment on Gradescope…") : ""}</div>${busy ? `<p class="supporting-copy" id="gradescope-busy-${h.e(kind)}">A send is running. You can look at a new preview, but the next send waits until this one finishes or is cancelled.</p>` : ""}` : ""}
    ${kind === "written" && plan.bubble?.sheets ? "" : mcMarkup(status.mc_check, h, configured)}
    ${links}
    ${kind === "written" ? creationsMarkup(status.creations, h, configured, status.automatic) : ""}
    ${latest.map(send => sendMarkup(send, earlier.length || status.coverage ? "Latest send (this send only)" : "Latest send", configured, h, kind)).join("")}
    ${earlier.length ? `<details class="disclosure grading-batch-disclosure grading-gradescope-earlier" data-gradescope-earlier><summary>Earlier sends · ${h.e(earlier.length)}</summary>${earlier.map(send => sendMarkup(send, "Earlier send", configured, h, kind)).join("")}</details>` : ""}`;
}

// THE MC CHECK (grading/gradescope_mc.py; requirement, 2026-10-06): Gradescope's last read of the bubble-sheet scores beside Faculty's own scoring, read after the written grades are
// sent or on Check MC. A student scored differently is named with both scores and the questions. MC CORRECTIONS (2026-10-22: "I want
// faculty's bubble sheet score to control", "correct it, but also flag the change"): Faculty changes Gradescope's score of each such
// question to its own, and every correction is listed here for the instructor to look at. SETTLED (2026-11-01): a correction a later
// read shows Gradescope agreeing with folds into "Earlier corrections"; the open ones stay in view.
const correctionWords = {accepted: "corrected on Gradescope", pending: "being corrected", sending: "being corrected", uncertain: "being checked on Gradescope",
  failed: "not corrected", refused: "not corrected"};

export function mcCorrectionsMarkup(corrections, h) {
  if (!(corrections || []).length) return "";
  const list = rows => `<ul class="grading-gradescope-links">${rows.map(c => `<li data-gradescope-mc-correction="${h.e(c.state)}"><strong>${h.e(c.name || "Student")}</strong> <span>Section ${h.e(c.section)} · Version ${h.e(c.version)} · Question ${h.e(c.number)}: Gradescope ${h.e(c.gradescope)} → ${h.e(c.faculty)}${c.why ? ` (${h.e(c.why)})` : ""} · ${h.e(correctionWords[c.state] || c.state)}${c.error ? `: ${h.e(c.error)}` : ""}</span></li>`).join("")}</ul>`;
  const open = corrections.filter(c => !c.settled), settled = corrections.filter(c => c.settled);
  const landed = open.filter(c => c.state === "accepted").length;
  const earlier = settled.length ? `<details class="disclosure" data-gradescope-mc-settled><summary>Earlier corrections Gradescope now agrees with · ${h.e(settled.length)}</summary>${list(settled)}</details>` : "";
  return `<div data-gradescope-mc-corrections>${open.length ? `<p class="supporting-copy"><strong>${h.e(count(landed, "question"))} corrected on Gradescope to your score.</strong> Look at each sheet; your score is the grade.</p>${list(open)}` : ""}${earlier}</div>`;
}

// MC LABELS (grading/gradescope_mc_labels.py; requirement, 2026-10-28): each
// answer's rubric item on Gradescope renamed to say whether it is right and what the answer is, by your key.
const labelWords = {accepted: "labelled", pending: "waiting", sending: "labelling", uncertain: "being checked on Gradescope", failed: "not finished", refused: "not finished"};

export function mcLabelsMarkup(labels, h) {
  if (!labels) return "";
  if (labels.running) return `<p class="supporting-copy" role="status" data-gradescope-mc-labels>Labelling answers on Gradescope…</p>`;
  const failed = (labels.sections || []).filter(s => s.state !== "accepted");
  const when = labels.at ? new Date(labels.at).toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"}) : "";
  return `<div data-gradescope-mc-labels><p class="supporting-copy">${failed.length ? "<strong>Answers are not all labelled on Gradescope.</strong>" : "<strong>Answers labelled on Gradescope.</strong>"} ${h.e(count(labels.renamed, "rubric item"))} renamed to show the correct answer${when ? ` · ${h.e(when)}` : ""}.</p>${failed.length
    ? `<ul class="grading-gradescope-links">${failed.map(s => `<li><strong>Section ${h.e(s.section)}</strong> <span>${h.e(labelWords[s.state] || s.state)}${s.error ? `: ${h.e(s.error)}` : ""}</span></li>`).join("")}</ul>` : ""}</div>`;
}

export function mcMarkup(mc, h, configured) {
  if (!mc) return "";
  const c = mc.counts || {}, differ = mc.differ || [];
  const when = mc.at ? new Date(mc.at).toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"}) : "";
  const label = configured && !mc.labels?.running ? h.button("gradescope-mc-labels", mc.labels ? "Label answers again" : "Label answers on Gradescope", 'data-secondary="true" title="Rename each answer’s rubric item so students see whether it is right and what the correct answer is"') : "";
  const action = configured && (!mc.running || label) ? `<div class="grading-actions">${!mc.running ? h.button("gradescope-mc-check", mc.checked ? "Check MC again" : "Check MC now", 'data-secondary="true"') : ""}${label}</div>` : "";
  const failed = (mc.failed || []).map(f => `<p class="grading-warning" role="alert">Section ${h.e(f.section)} could not be read: ${h.e(f.error)}</p>`).join("");
  let body;
  if (mc.running) body = `<p class="supporting-copy" role="status">Reading Gradescope’s multiple-choice scores…${mc.waiting ? ` ${h.e(mc.waiting)}` : ""}</p>`;
  else if (!mc.checked) body = `<p class="supporting-copy">Not compared yet. Faculty reads Gradescope’s scores after the written grades are sent, or when you check now.</p>`;
  else {
    const others = [c.not_on_gradescope ? `${count(c.not_on_gradescope, "sheet")} Faculty scored not on Gradescope` : "",
      c.not_graded ? `${count(c.not_graded, "sheet")} not graded there yet` : "", c.faculty_unscored ? `${count(c.faculty_unscored, "sheet")} Faculty has no score for` : "",
      c.unmatched ? `${count(c.unmatched, "student")} on Gradescope Faculty cannot match` : ""].filter(Boolean);
    const open = differ.filter(d => !d.corrected);
    const lead = open.length ? `<strong>${h.e(count(open.length, "student"))} scored differently on Gradescope.</strong>`
      : differ.length ? "<strong>Faculty is correcting Gradescope’s scores to yours.</strong>" : "<strong>Gradescope and Faculty agree.</strong>";
    body = `<p class="supporting-copy" data-gradescope-mc-summary>${lead} ${h.e(number(c.agree))} of ${h.e(count(c.compared, "sheet"))} compared match${when ? ` · read ${h.e(when)}` : ""}${(mc.sections || []).length ? ` · ${(mc.sections.length === 1 ? "Section " : "Sections ") + mc.sections.map(h.e).join(", ")}` : ""}.</p>
      ${others.length ? `<p class="supporting-copy">${h.e(others.join(" · "))}.</p>` : ""}
      ${mcCorrectionsMarkup(mc.corrections, h)}
      ${open.length ? `<ul class="grading-gradescope-links" data-gradescope-mc-differ>${open.map(d => `<li><strong>${h.e(d.name || "Student")}</strong> <span>Section ${h.e(d.section)} · Gradescope ${h.e(d.gradescope?.total ?? "—")}${d.gradescope?.version ? ` (Version ${h.e(d.gradescope.version)})` : ""}, Faculty ${h.e(d.faculty?.total ?? "—")}${d.faculty?.version ? ` (Version ${h.e(d.faculty.version)})` : ""}</span>${(d.questions || []).length
        ? `<ul>${d.questions.map(q => `<li>Question ${h.e(q.number)}: Gradescope ${h.e(q.gradescope)}, Faculty ${h.e(q.faculty)} · ${h.e(q.why)}</li>`).join("")}</ul>` : `<p>${h.e(d.detail)}</p>`}</li>`).join("")}</ul>
        <p class="supporting-copy">Your score is the grade. Faculty corrects a question’s score on Gradescope each time it reads Gradescope’s scores, but never a sheet Gradescope filed under another version: change those there by hand. Check again to retry one that was not corrected.</p>` : ""}`;
  }
  return `<section class="grading-gradescope-section grading-gradescope-mc" data-gradescope-mc aria-label="Multiple choice compared with Gradescope"><h4>Multiple choice on Gradescope</h4>${body}${failed}${mcLabelsMarkup(mc.labels, h)}${action}</section>`;
}

// One kind of unit (papers or grades): how many are finished (nothing more will happen to them in this send), and every state
// in plain words, so a finished paper that did not go through is never read as one on Gradescope.
export function unitProgress(label, counts, h) {
  const total = sum(counts);
  if (!total) return "";
  const open = openStates.reduce((n, state) => n + Number(counts?.[state] || 0), 0), done = total - open;
  const facts = unitOrder.filter(state => counts?.[state]).map(state => `<span${state === "failed" ? ' class="grading-batch-failed"' : ""} data-gradescope-state="${h.e(state)}"><b>${h.e(number(counts[state]))}</b> ${h.e(unitWords[state])}</span>`).join("");
  return `<div class="grading-gradescope-kind"><div class="grading-batch-progress"><p><b>${h.e(number(done))}</b> of ${h.e(count(total, label))} finished</p><span>${h.e(Math.floor(done / total * 100))}%</span></div><progress max="${h.e(total)}" value="${h.e(done)}" aria-label="${h.e(`${label[0].toUpperCase()}${label.slice(1)}s finished`)}"></progress><div class="grading-batch-facts">${facts}</div></div>`;
}

function sendMarkup(send, caption, configured, h, kind = "written") {
  const counts = send.counts || {}, uploads = counts.uploads || {}, grades = counts.grades || {}, sheets = counts.sheets || {};
  // What the instructor may want to look at: everything that did not simply land. Cancelled units are counted above and not listed.
  const problems = ["uploads", "grades", "sheets"].reduce((n, unit) => n + Object.entries(counts[unit] || {}).filter(([state]) => !quiet.includes(state)).reduce((m, [, v]) => m + Number(v || 0), 0), 0);
  const failed = Number(uploads.failed || 0) + Number(grades.failed || 0) + Number(sheets.failed || 0);
  const when = send.created_at ? new Date(send.created_at).toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"}) : "";
  const approved = send.plan?.summary || {};
  const targets = (send.plan?.targets || []).filter(t => t.container);
  const leftOut = (send.plan?.targets || []).filter(t => t.left_out);
  const published = targets.filter(t => t.published);
  // Sections by the exam they went to: four sections of one exam read as one route, not four.
  const routes = Object.entries(targets.reduce((by, t) => ((by[t.container] ||= []).push(t.section), by), {}))
    .map(([exam, sections]) => `${sections.length === 1 ? "Section" : "Sections"} ${sections.map(h.e).join(", ")} → ${h.e(exam)}`).join("; ");
  // A send that is over and left nothing to look at says what landed in one line; bars are for a send still moving or one with problems.
  const settled = !live(send) && !problems;
  const progress = settled ? `<p class="grading-batch-facts grading-gradescope-settled">${["uploads", "grades", "sheets"].flatMap(unit => Object.entries(counts[unit] || {}).filter(([, v]) => Number(v))
      .map(([state, v]) => `<span data-gradescope-state="${h.e(state)}"><b>${h.e(number(v))}</b> ${h.e(`${{uploads: "paper", grades: "grade", sheets: "sheet"}[unit]}${Number(v) === 1 ? "" : "s"}`)} ${h.e(unitWords[state] || state)}</span>`)).join("")}</p>`
    : `${unitProgress("paper", uploads, h)}${unitProgress("grade", grades, h)}${unitProgress("sheet", sheets, h)}`;
  const actions = [live(send) ? h.button("gradescope-cancel", "Cancel") : "",
    failed && send.status !== "cancelled" && configured ? h.button("gradescope-retry", `Retry failed (${number(failed)})`) : ""].filter(Boolean).join("");
  return `<article class="grading-gradescope-send" data-gradescope-send="${h.e(send.id)}" aria-label="${h.e(`${caption}${when ? `, ${when}` : ""}`)}">
    <header><span class="grading-batch-caption">${h.e(caption)}${when ? ` · ${h.e(when)}` : ""}</span><h4>${h.e(sendWords[send.status] || send.status)}</h4></header>
    ${approved.papers || approved.grades || approved.sheets ? `<p class="supporting-copy">Approved: ${kind === "sheets" ? h.e(count(approved.sheets?.upload, "sheet")) + " to upload"
      : send.plan?.papers_only ? `${h.e(count(approved.papers?.upload, "paper"))} to upload, papers only (no grades)`
      : `${h.e(count(approved.papers?.upload, "paper"))} to upload and ${h.e(count(Number(approved.grades?.send || 0) + Number(approved.grades?.update || 0) + Number(approved.grades?.replace || 0), "grade"))} to set${Number(send.plan?.replacing) ? `, ${h.e(count(send.plan.replacing, "grade"))} someone else made on Gradescope replaced` : ""}`}${routes ? ` · ${routes}` : ""}${leftOut.length ? ` · left out: ${leftOut.map(t => `Section ${h.e(t.section)}`).join(", ")}` : ""}${published.length ? ` · confirmed although grades were published: ${published.map(t => `Section ${h.e(t.section)}`).join(", ")}` : ""}.</p>` : ""}
    ${send.plan?.automatic ? automaticSendMarkup(send.plan.automatic, h) : ""}
    ${progress}
    ${send.waiting && live(send) ? `<p class="grading-batch-note" role="status">${h.e(send.waiting)}</p>` : ""}
    ${actions ? `<div class="grading-actions">${actions}</div>` : ""}
    ${problems ? `<details class="disclosure grading-batch-disclosure" data-gradescope-problems="${h.e(send.id)}"><summary>Show problems · ${h.e(number(problems))}</summary><div data-gradescope-problem-list data-patch-static="problems:${h.e(send.id)}"></div></details>` : ""}
  </article>`;
}

// A send Faculty made by itself after certifying a late paper (GRADESCOPE "AUTOMATIC LATE-PAPER SEND"). A makeup version Faculty added to
// a published set lands unpublished, so the student's version may not be published although the set is: the card says so and points
// to your own Publish grades (the header's; PUBLISHING GRADES). Faculty never publishes by itself.
export function automaticSendMarkup(automatic, h) {
  // After your own Certify with its Gradescope tick (GRADESCOPE "AFTER CERTIFYING"): Faculty sent what was missing and publishes next.
  if (automatic.after === "certified") return `<p class="supporting-copy" data-gradescope-automatic>Sent by Faculty by itself, after you certified${automatic.published ? " (grades were already published there)" : ""}.</p>`;
  const hidden = automatic.unpublished_version;
  return `<p class="supporting-copy" data-gradescope-automatic>Sent by Faculty by itself, after it certified a late paper${automatic.published && !hidden ? ", although grades are published there (as you chose)" : ""}.</p>`
    + (hidden ? `<p class="grading-warning" role="status" data-gradescope-unpublished>Version ${h.e(hidden)} is not published on Gradescope, so the student does not see this grade yet · ${h.e(PUBLISH_WHERE)}</p>` : "");
}

export function problemsMarkup(listed, plan, h, kind = "written") {
  const problems = listed.filter(p => !quiet.includes(p.status));
  if (!problems.length) return '<p class="grading-gradescope-empty">Nothing in this send is waiting for you.</p>';
  const what = p => kind === "sheets" ? `Sheet${p.page ? ` (scan page ${p.page})` : ""}` : p.question ? questionTitle(plan, p.question) : "Paper";
  return tableMarkup(h.e, ["Student", "Section", "What", "Status", "Reason"], problems.map(p =>
    `<tr><td>${h.e(p.name || "Unnamed student")}</td><td>${h.e(p.section ?? "")}</td><td>${h.e(what(p))}</td><td>${h.e(unitWords[p.status] || p.status)}</td><td>${h.e(p.reason || "")}</td></tr>`), kind === "sheets" ? "Sheets that did not go to Gradescope" : "Papers and grades that did not go to Gradescope");
}

// --- the dialog -------------------------------------------------------------------------------------------------------------
export function summaryMarkup(preview, h) {
  const s = preview.summary || {}, papers = s.papers || {}, grades = s.grades || {};
  const item = (n, text) => Number(n) ? `<li><b>${h.e(number(n))}</b> ${h.e(text)}</li>` : "";
  if (s.sheets) return `<div class="grading-gradescope-summary" data-gradescope-summary>
    <section><h4>Bubble sheets</h4><p><strong>${h.e(number(s.sheets.upload))}</strong> to upload</p><ul>${item(s.sheets.already, "already on Gradescope (uploaded by Faculty earlier)")}${item(s.sheets.repeats, "scanned twice: the same sheet in a later scan, so only its first copy goes")}${item(s.sheets.skipped, "skipped (listed below)")}${item(s.sheets.left_out, "in sections left out of this send")}</ul></section>
    <section><h4>Read in the Inbox</h4><p><strong>${h.e(number(s.read))}</strong> from reviewed scans</p><ul>${item(s.waiting, "more in scans still waiting for scan checks (not sent)")}</ul></section>
  </div>`;
  // Who made the grades already there exactly as Faculty's (a TA, or the instructor on Gradescope): Faculty never sent them.
  const sameBy = (preview.replacing?.same_by || []).map(b => `${b.name}: ${number(b.grades)}`).join(", ");
  const paperSection = `<section><h4>Papers</h4><p><strong>${h.e(number(papers.upload))}</strong> to upload</p><ul>${item(papers.already, "already on Gradescope (uploaded by Faculty earlier)")}${item(papers.skipped, "skipped (listed below)")}${item(papers.left_out, "in sections left out of this send")}</ul></section>`;
  if (preview.papers_only) return `<div class="grading-gradescope-summary" data-gradescope-summary>
    ${paperSection}
    <section data-gradescope-papers-only><h4>Grades</h4><p><strong>0</strong> sent · papers only</p><ul><li>No rubric item is ticked and no score is set.${Number(s.final) ? ` <b>${h.e(number(s.final))}</b> final ${Number(s.final) === 1 ? "grade stays" : "grades stay"} in Faculty until you send papers and grades.` : ""}</li></ul></section>
  </div>`;
  return `<div class="grading-gradescope-summary" data-gradescope-summary>
    ${paperSection}
    <section><h4>Grades</h4><p><strong>${h.e(number(Number(grades.send || 0) + Number(grades.update || 0) + Number(grades.replace || 0)))}</strong> to send</p><ul>${item(grades.replace, "of them replace a grade someone else made on Gradescope (listed below)")}${item(preview.replacing?.same, `of them were already graded on Gradescope by someone else, exactly as Faculty graded them: nothing changes there${sameBy ? ` (by ${sameBy})` : ""}`)}${item(grades.update, "of them replace a grade Faculty sent earlier")}${item(grades.already, "already on Gradescope")}${item(grades.refused, "refused (listed below)")}${item(grades.held, "held because the paper was not sent")}${item(grades.left_out, "in sections left out of this send")}${item(s.not_final, `of ${number(s.responses)} recorded responses have no final grade yet and are not sent`)}</ul></section>
  </div>`;
}

export function questionsMarkup(preview, plan, h) {
  const rows = preview.questions || [];
  if (!rows.length) return "";
  const replacing = Boolean(preview.replace_others);
  return `<h4 class="grading-gradescope-heading">Per question</h4>${tableMarkup(h.e, ["Faculty question", "Gradescope question", "To send", ...(replacing ? ["Replacing"] : []), "Already there", "Refused", "Held", "Why refused"], rows.map(q =>
    `<tr><td>${h.e(questionTitle(plan, q.faculty, q.version || ""))}</td><td>${h.e(q.gradescope || "Not matched")}</td><td>${h.e(number(Number(q.send || 0) + Number(q.update || 0) + Number(q.replace || 0)))}</td>${replacing ? `<td>${h.e(number(q.replace))}</td>` : ""}<td>${h.e(number(q.already))}</td><td>${h.e(number(q.refused))}</td><td>${h.e(number(q.held))}</td><td>${(q.reasons || []).length ? `<ul class="grading-gradescope-reasons">${q.reasons.map(r => `<li>${h.e(number(r.count))} × ${h.e(r.reason)}</li>`).join("")}</ul>` : ""}</td></tr>`), "What is sent per question")}`;
}

export function listsMarkup(preview, plan, h) {
  const s = preview.summary || {}, parts = [];
  const note = (shown, total) => total > shown ? `<p class="supporting-copy">Showing the first ${h.e(number(shown))} of ${h.e(number(total))}.</p>` : "";
  const replacing = preview.replacing || {};
  if ((replacing.listed || []).length) parts.push(`<details class="disclosure grading-gradescope-list" data-gradescope-replacing open><summary>Grades this send replaces · ${h.e(number(replacing.grades))}</summary>${note(replacing.listed.length, replacing.grades || 0)}${tableMarkup(h.e, ["Student", "Section", "Question", "On Gradescope", "Graded there by", "Faculty's grade"], replacing.listed.map(r => `<tr><td>${h.e(r.name || "Unnamed student")}</td><td>${h.e(r.section ?? "")}</td><td>${h.e(questionTitle(plan, r.question))}</td><td>${h.e(r.gradescope ?? "no score")}</td><td>${h.e(r.by || "not named")}</td><td>${h.e(r.faculty)}</td></tr>`), "Grades someone else made on Gradescope that this send replaces")}</details>`);
  if ((preview.warnings || []).length) parts.push(`<details class="disclosure grading-gradescope-list" data-gradescope-warnings><summary>Warnings · ${h.e(preview.warnings.length)}</summary><ul class="grading-gradescope-notes">${preview.warnings.map(w => `<li>${h.e(w)}</li>`).join("")}</ul></details>`);
  if ((preview.skipped || []).length) parts.push(`<details class="disclosure grading-gradescope-list" data-gradescope-skipped><summary>Skipped ${s.sheets ? "sheets" : "papers"} · ${h.e(number((s.sheets || s.papers)?.skipped ?? preview.skipped.length))}</summary>${note(preview.skipped.length, (s.sheets || s.papers)?.skipped || 0)}${tableMarkup(h.e, ["Student", "Section", "Reason"], preview.skipped.map(p => `<tr><td>${h.e(p.name || (p.page ? `Sheet on scan page ${p.page}` : "Unnamed student"))}</td><td>${h.e(p.section ?? "")}</td><td>${h.e(p.reason || "")}</td></tr>`), s.sheets ? "Sheets that are not uploaded" : "Papers that are not uploaded")}</details>`);
  if ((preview.refused || []).length) parts.push(`<details class="disclosure grading-gradescope-list" data-gradescope-refused><summary>Refused grades · ${h.e(number(s.grades?.refused ?? preview.refused.length))}</summary>${note(preview.refused.length, s.grades?.refused || 0)}${tableMarkup(h.e, ["Student", "Section", "Question", "Reason"], preview.refused.map(p => `<tr><td>${h.e(p.name || "Unnamed student")}</td><td>${h.e(p.section ?? "")}</td><td>${h.e(questionTitle(plan, p.question))}</td><td>${h.e(p.reason || "")}</td></tr>`), "Grades that are not sent")}</details>`);
  return parts.join("");
}

// A section whose Gradescope course is its own saved link (set on its course page, or by an earlier send) shows that course as a
// fact with Change…; `changing` holds the sections whose picker the instructor opened with it (browser memory only).
export function sectionMarkup(section, courses, h, kind = "written", changing = new Set()) {
  const listed = courses.filter(c => !forbidden(c));
  const known = listed.some(c => String(c.id) === String(section.course_id));
  const fixed = Boolean(section.linked) && known && !changing.has(String(section.section_id));
  const courseOptions = `<option value="" ${known ? "disabled" : "selected"}>Choose a Gradescope course…</option>${listed.map(c => `<option value="${h.e(c.id)}" ${String(c.id) === String(section.course_id) ? "selected" : ""}>${h.e([c.short, c.name].filter(Boolean).join(" · "))}${c.term ? ` (${h.e(c.term)})` : ""}</option>`).join("")}`;
  const containers = section.containers || [];
  const chosen = containers.some(c => String(c.id) === String(section.container_id));
  const examOptions = `<option value="" ${chosen ? "disabled" : "selected"}>${containers.length ? "Choose the assignment…" : known ? "No assignment in this course" : "Choose the course first"}</option>${containers.map(c => {
    const letters = (c.versions || []).filter(Boolean);
    return `<option value="${h.e(c.id)}" ${String(c.id) === String(section.container_id) ? "selected" : ""}>${h.e(c.title)}${letters.length ? ` (versions ${h.e(letters.join(", "))})` : ""}</option>`;
  }).join("")}`;
  const id = h.e(section.section_id), out = Boolean(section.left_out);
  // Leaving a section out (requirement, 2026-09-25: send one section first and check it) keeps its choices for when it is included.
  return `<fieldset class="grading-gradescope-section" data-gradescope-section="${id}"${out ? ' data-left-out="true"' : ""}><legend>Section ${h.e(section.section)}</legend><div class="grading-batch-fields">
    ${fixed ? `<div class="field">Gradescope course<span class="grading-gradescope-fixed" data-gradescope-linked="${id}">${h.e(section.course || "")}<button type="button" class="text-button" data-gradescope-change="${id}" aria-label="${h.e(`Change the Gradescope course for section ${section.section}`)}">Change…</button></span></div>`
      : `<label class="field">Gradescope course<select data-gradescope-course="${id}" aria-label="${h.e(`Gradescope course for section ${section.section}`)}"${out ? ' data-left-out="true"' : ""}>${courseOptions}</select></label>`}
    <label class="field">Gradescope assignment<select data-gradescope-container="${id}" aria-label="${h.e(`Gradescope assignment for section ${section.section}`)}" ${containers.length && !out ? "" : 'data-empty="true"'}>${examOptions}</select></label></div>
    <label class="grading-check grading-gradescope-leave"><input type="checkbox" data-gradescope-leave="${id}" ${out ? "checked" : ""}>Leave this section out of this send</label>
    ${out ? '<p class="supporting-copy" role="status">Left out: none of this section’s papers or grades are sent this time. Untick to include it.</p>' : ""}
    ${!out && section.proposed && !section.problem ? `<p class="supporting-copy">${h.e(KINDS[kind].proposed)}</p>` : ""}
    ${!out && section.warning ? `<p class="grading-warning" role="status" data-gradescope-published="${id}">${h.e(section.warning)}</p>` : ""}
    ${!out && section.problem ? `<p class="grading-warning" role="status">${h.e(section.problem)}</p>` : ""}</fieldset>`;
}

// THE BUBBLE SHEETS TOO (requirement, 2026-10-26): the written
// Send dialog's Bubble sheets part, from the Bubble sheets page's own preview: its counts, its sections' problems and skipped sheets,
// and a tick (on by default) that sends them with this send, first. Shown only when the assignment has bubble sheets read.
export const hasSheets = sheets => Boolean(sheets && !sheets.error && sheets.configured !== false && (Number(sheets.summary?.read || 0) || Number(sheets.summary?.waiting || 0) || (sheets.sections || []).length));
export function sheetsPartMarkup(sheets, wanted, h) {
  if (!sheets) return '<section class="grading-gradescope-sheets" data-gradescope-sheets><h4 class="grading-gradescope-heading">Bubble sheets</h4><p class="loading-state" role="status">Reading the bubble sheets…</p></section>';
  if (sheets.error) return `<section class="grading-gradescope-sheets" data-gradescope-sheets><h4 class="grading-gradescope-heading">Bubble sheets</h4><p class="grading-warning" role="status">Faculty could not read the bubble sheets: ${h.e(sheets.error)} Send them from the Bubble sheets page.</p></section>`;
  if (!hasSheets(sheets)) return "";
  const s = sheets.summary?.sheets || {}, upload = Number(s.upload || 0);
  const problems = (sheets.sections || []).filter(x => x.problem && !x.left_out);
  const box = upload && !sheets.running
    ? `<label class="grading-check"><input type="checkbox" name="gradescope-sheets" ${wanted ? "checked" : ""}>Also upload ${h.e(count(upload, "bubble sheet"))} to the bubble-sheet exam, before the papers</label>` : "";
  return `<section class="grading-gradescope-sheets" data-gradescope-sheets><h4 class="grading-gradescope-heading">Bubble sheets</h4>
    ${box}
    <p class="supporting-copy">${h.e(number(upload))} to upload${Number(s.already) ? ` · ${h.e(number(s.already))} already on Gradescope` : ""}${Number(s.repeats) ? ` · ${h.e(number(s.repeats))} scanned twice (only the first copy goes)` : ""}${Number(s.skipped) ? ` · ${h.e(number(s.skipped))} skipped` : ""}${Number(sheets.summary?.waiting) ? ` · ${h.e(number(sheets.summary.waiting))} in scans still waiting for scan checks` : ""}. Gradescope scores them with its own key; no grade is sent for them.</p>
    ${sheets.running ? '<p class="grading-warning" role="status">A send of bubble sheets is still running; they are not part of this send.</p>' : ""}
    ${problems.map(x => `<p class="grading-warning" role="status">Section ${h.e(x.section)}: ${h.e(x.problem)} Choose it on the Bubble sheets page.</p>`).join("")}
    ${(sheets.published || []).length && upload && wanted ? `<p class="grading-warning" role="status">Grades of the bubble-sheet exam are published for ${sheets.published.map(p => `Section ${h.e(p.section)}`).join(", ")}: sending asks you to confirm.</p>` : ""}
    ${(sheets.skipped || []).length ? `<details class="disclosure grading-gradescope-list" data-gradescope-sheets-skipped><summary>Skipped sheets · ${h.e(number(s.skipped ?? sheets.skipped.length))}</summary>${tableMarkup(h.e, ["Student", "Section", "Reason"], sheets.skipped.map(p => `<tr><td>${h.e(p.name || (p.page ? `Sheet on scan page ${p.page}` : "Unnamed student"))}</td><td>${h.e(p.section ?? "")}</td><td>${h.e(p.reason || "")}</td></tr>`), "Sheets that are not uploaded")}</details>` : ""}
  </section>`;
}

// --- sending to an exam whose grades are published ------------------------------------------------------------------------
// What each published exam of the preview receives, in the popup that asks before sending there. The server names only the exams
// this send would write to, with how many papers and grades (or sheets) go to each.
export function publishedMarkup(published, h, kind = "written") {
  const exams = new Set(published.map(p => String(p.container_id))).size, one = exams === 1;
  const what = p => kind === "sheets" ? count(p.sheets, "sheet")
    : [p.papers ? count(p.papers, "paper") : "", p.grades ? count(p.grades, "grade") : ""].filter(Boolean).join(" and ");
  return `<header class="dialog-header"><div><h2 id="gradescope-published-title">Send to ${one ? "a published assignment" : "published assignments"}?</h2><p>Grades on ${one ? "this Gradescope assignment are" : "these Gradescope assignments are"} already published to students.</p></div></header>
    <div class="dialog-body"><p>Students can see ${kind === "sheets" ? "each sheet and the score Gradescope gives it" : "each paper and grade"} as soon as Faculty writes it.</p>
    <ul class="grading-gradescope-published">${published.map(p => `<li><strong>Section ${h.e(p.section)}</strong> → ${h.e(p.container)}${p.course ? ` <span>(${h.e(p.course)})</span>` : ""}${what(p) ? `: ${h.e(what(p))}` : ""}</li>`).join("")}</ul>
    <p class="supporting-copy">Faculty itself still publishes nothing and sends no email; the only comment is a grade's lines added on the certification page. To keep this from students for now, cancel, then unpublish the grades on Gradescope or leave ${published.length === 1 ? "this section" : "these sections"} out of this send.</p></div>
    <footer class="dialog-footer"><button type="button" class="button" data-published-cancel>Cancel</button><button type="button" class="button danger" data-published-confirm>Send to ${one ? "the published assignment" : "the published assignments"}</button></footer>`;
}

// The popup itself, over the send dialog: resolves true only for its confirm button. Escape, Cancel and closing the send dialog
// all answer no. It is the question, not the control: the server refuses a send that does not name each published exam.
export function confirmPublished(published, h, kind = "written", parent = null) {
  return confirmPopup(publishedMarkup(published, h, kind), "gradescope-published-title", "published", parent);
}

// --- replacing grades someone else made on Gradescope ---------------------------------------------------------------------
// The popup that asks before a send replaces them (requirement, 2026-10-01): how many, who made them there, how many change a score.
export function replacingMarkup(replacing, h) {
  const n = Number(replacing.grades || 0), changes = Number(replacing.score_changes || 0);
  return `<header class="dialog-header"><div><h2 id="gradescope-replacing-title">Replace ${h.e(count(n, "grade"))} on Gradescope?</h2><p>${n === 1 ? "This grade was" : "These grades were"} made on Gradescope, not by Faculty, and ${n === 1 ? "differs" : "differ"} from Faculty's final ${n === 1 ? "grade" : "grades"}.</p></div></header>
    <div class="dialog-body"><ul class="grading-gradescope-published" data-gradescope-replacing-by>${(replacing.by || []).map(b => `<li><strong>${h.e(b.name)}</strong>: ${h.e(count(b.grades, "grade"))}</li>`).join("")}</ul>
    <p>${changes ? `${h.e(count(changes, "score"))} ${changes === 1 ? "changes" : "change"}${n - changes ? `; the other ${h.e(count(n - changes, "grade"))} ${n - changes === 1 ? "keeps its score" : "keep their scores"} and ${n - changes === 1 ? "differs" : "differ"} only in rubric items, adjustment or comment.` : "."}` : "No score changes: they differ only in rubric items, adjustment or comment."} A comment made there is replaced too.</p>
    <p class="supporting-copy">Faculty replaces each one only while Gradescope still shows exactly what the preview showed, and keeps what was there in its receipt. A grade changed on Gradescope after this preview is left alone and listed with the send's problems, so fewer may be replaced.</p></div>
    <footer class="dialog-footer"><button type="button" class="button" data-replacing-cancel>Cancel</button><button type="button" class="button danger" data-replacing-confirm>Replace ${h.e(count(n, "grade"))}</button></footer>`;
}

// Like `confirmPublished`: true only for its confirm button; the server refuses a send that does not name how many it replaces.
export function confirmReplacing(replacing, h, parent = null) {
  return confirmPopup(replacingMarkup(replacing, h), "gradescope-replacing-title", "replacing", parent);
}

// The choice itself, under What to send: off by default and in every new dialog; papers only sets no grade, so it is off there.
export function replaceMarkup(replace, only) {
  return `<div class="grading-gradescope-replace" data-gradescope-replace-choice><label class="checkbox-field"><input type="checkbox" name="gradescope-replace" ${replace && !only ? "checked" : ""} ${only ? "disabled" : ""}>Replace grades someone else made on Gradescope</label>
    <p class="supporting-copy">${only ? "Papers only sets no grade, so it replaces none." : "Off, a question someone already graded there (a TA, or you on Gradescope) is left alone. On, the preview reads each question on Gradescope and lists the grades that differ from Faculty's; sending replaces exactly those, after you confirm."}</p></div>`;
}

// What a written send carries: papers with their final grades, or the papers alone (requirement, 2026-09-28).
export function modeMarkup(only, h) {
  const choice = (value, title, text) => `<label class="compute-mode"><input type="radio" name="gradescope-what" value="${value}" ${(value === "papers") === only ? "checked" : ""}><span><strong>${h.e(title)}</strong><small>${h.e(text)}</small></span></label>`;
  return `<fieldset class="compute-modes grading-gradescope-mode" data-gradescope-mode><legend class="grading-gradescope-heading">What to send</legend>
    ${choice("grades", "Papers and final grades", "Uploads each paper and ticks the rubric items of its final grade.")}
    ${choice("papers", "Papers only", "Uploads each paper, matched to its student, and sets no grade. Send the grades later.")}</fieldset>`;
}

export function sendDialog(plan, h, onSent, kind = "written", {papersOnly = false} = {}) {
  let preview = null, previewTargets = {}, choices = {}, requestId = "", generation = 0, loading = false, sending = false;
  // The Bubble sheets part (THE BUBBLE SHEETS TOO): its own preview, read beside this one; `sheetsWanted` is the tick, on by default.
  let sheets = null, sheetsWanted = true;
  const withSheets = kind === "written";
  const sheetsGo = () => withSheets && sheetsWanted && hasSheets(sheets) && !sheets.running && Boolean(sheets.fingerprint) && sendable(sheets) > 0;
  let only = kind === "written" && Boolean(papersOnly);  // papers without grades: part of every preview and send request
  let replace = false;  // replace grades someone else made on Gradescope: asked for each send, never remembered
  const changing = new Set();  // sections whose linked course the instructor chose to change in this dialog
  const words = KINDS[kind], key = `${kind}:${plan.id}`;
  const form = h.dialog(words.dialog, `Faculty reads Gradescope and shows what it would upload${kind === "sheets" ? "" : " and grade"}. Nothing is written until you tick the box and send.`,
    `${kind === "written" ? `${modeMarkup(only, h)}<div data-gradescope-replace-slot>${replaceMarkup(replace, only)}</div>` : ""}<div class="grading-gradescope-choices" data-gradescope-targets><p class="loading-state" role="status">Reading your Gradescope courses…</p></div><div data-gradescope-outlook aria-live="polite"></div>`,
    words.dialog, async () => {});
  form.classList.add("grading-batch-form");
  const active = () => form.isConnected && form.closest("dialog")?.open;
  const targetsSlot = form.querySelector("[data-gradescope-targets]"), outlook = form.querySelector("[data-gradescope-outlook]");
  const error = form.querySelector(".form-error");
  const submit = form.querySelector('button[type="submit"]');
  const label = () => { submit.textContent = only ? "Upload papers" : words.dialog; };
  const replaceSlot = form.querySelector("[data-gradescope-replace-slot]");
  const drawReplace = () => { if (replaceSlot) replaceSlot.innerHTML = replaceMarkup(replace, only); };
  // What every preview and send of this dialog asks, beyond the sections: papers only, or replacing others' grades (never both).
  const mode = () => only ? {papers_only: true} : replace ? {replace_others: true} : {};
  label();
  const retry = document.createElement("button");
  retry.type = "button"; retry.className = "button primary"; retry.textContent = "Retry request"; retry.hidden = true;
  retry.dataset.gradescopeRetry = "";
  submit.before(retry);
  const check = document.createElement("button");
  check.type = "button"; check.className = "button"; check.textContent = "Check again";
  check.dataset.gradescopeRefresh = "";
  retry.before(check);

  // Belt and braces for the coordinator's course: the server refuses it before any request, and this page never names it either.
  const refusedCourses = result => new Set((result?.courses || []).filter(forbidden).map(c => String(c.id)));
  function blocked() {
    if (unresolved.has(key)) return "An earlier send request is not confirmed yet. Use Retry request.";
    if (sending) return "Sending…";
    if (loading || !preview) return "Checking Gradescope…";
    if (!preview.configured) return "Gradescope is not set up.";
    if (preview.running) return "A send to Gradescope is still running for this assignment.";
    if (!preview.fingerprint) return "The preview is not complete.";
    if (!sendable(preview) && !sheetsGo()) return "There is nothing to send.";
    const refused = refusedCourses(preview);
    if (Object.values(preview.targets || {}).some(t => refused.has(String(t.course_id)))) return "Faculty never sends anything to Math 140 for Instructors.";
    return "";
  }
  function controls() {
    const reason = blocked(), pending = unresolved.has(key);
    submit.disabled = Boolean(reason);
    submit.title = reason;
    retry.hidden = !pending;
    retry.disabled = sending;
    check.hidden = pending;
    check.disabled = loading || sending;
    form.querySelectorAll("[data-gradescope-course], [data-gradescope-container]").forEach(select => {
      select.disabled = loading || sending || pending || select.dataset.empty === "true" || select.dataset.leftOut === "true";
    });
    form.querySelectorAll("[data-gradescope-leave], [data-gradescope-change], [name='gradescope-what']").forEach(box => { box.disabled = loading || sending || pending; });
    form.querySelectorAll("[name='gradescope-replace']").forEach(box => { box.disabled = only || loading || sending || pending; });
    form.querySelectorAll("[name='gradescope-sheets']").forEach(box => { box.disabled = loading || sending || pending; });
  }
  const finish = () => { if (active()) controls(); };

  async function loadPreview(notice = "") {
    const token = ++generation;
    const requested = JSON.parse(JSON.stringify(choices));
    const current = () => active() && token === generation;
    preview = null; loading = true; requestId = "";
    error.textContent = ""; error.hidden = true;
    outlook.innerHTML = `${notice}<p class="loading-state" role="status">Reading Gradescope and Faculty${replace && !only ? ", and every graded question on Gradescope" : ""}… this can take a minute.</p>`;
    form.setAttribute("aria-busy", "true");
    controls();
    let result;
    // The bubble sheets' own preview, read at the same time (their sections go to their own linked or proposed exams).
    sheets = null;
    const sheetsRead = withSheets ? h.api(`${base(plan, "sheets")}/preview`, "POST", {targets: {}}, {}, WAIT).catch(cause => ({error: cause.message})) : null;
    try {
      [result, sheets] = await Promise.all([h.api(`${base(plan, kind)}/preview`, "POST", {targets: requested, ...mode()}, {}, WAIT), sheetsRead]);
    } catch (cause) {
      if (current()) {
        loading = false;
        outlook.innerHTML = `${notice}<p role="alert">${h.e(cause.message)}</p><p class="supporting-copy">Nothing was sent. Choose Check again to read Gradescope once more.</p>`;
        if (targetsSlot.querySelector(".loading-state")) targetsSlot.innerHTML = "";
        form.removeAttribute("aria-busy");
        controls();
      }
      return;
    }
    if (!current()) return;
    loading = false;
    form.removeAttribute("aria-busy");
    preview = result; previewTargets = requested; requestId = crypto.randomUUID();
    const refused = refusedCourses(result);
    choices = Object.fromEntries((result.sections || []).filter(s => s.left_out || (s.course_id && !refused.has(String(s.course_id))))
      .map(s => [s.section_id, {...(s.left_out ? {leave_out: true} : {}), course_id: refused.has(String(s.course_id)) ? "" : String(s.course_id || ""),
                                container_id: String(s.container_id || "")}]));
    draw(result, notice);
    controls();
  }

  function draw(result, notice = "") {
    if (!result.configured) {
      targetsSlot.innerHTML = "";
      outlook.innerHTML = `<div class="grading-warning" role="status"><p><strong>Gradescope is not set up for this Faculty.</strong> ${h.e(result.problem || "")}</p><p>Run <code>python3 ~/.config/faculty/save-gradescope.py</code> in a terminal; the Faculty service must name the saved file and be restarted.</p></div>`;
      return;
    }
    const sections = result.sections || [];
    targetsSlot.innerHTML = sections.length
      ? `<h4 class="grading-gradescope-heading">Where each section goes</h4><div class="grading-gradescope-sections">${sections.map(s => sectionMarkup(s, result.courses || [], h, kind, changing)).join("")}</div><p class="supporting-copy">${h.e(words.route)}</p>`
      : `<p class="grading-gradescope-empty">No section of this assignment has ${kind === "sheets" ? "a matched sheet" : "a paper"} to send.</p>`;
    const nothing = !sendable(result) && !sheetsGo(), published = result.published || [];
    const publishedNote = published.length ? `<p class="grading-warning" role="status" data-gradescope-published-note>Grades are already published on Gradescope for ${published.map(p => `Section ${h.e(p.section)}`).join(", ")}: students see what Faculty writes there at once. Sending asks you to confirm.</p>` : "";
    const replacing = Number(result.replacing?.grades || 0);
    const replacingNote = replacing ? `<p class="grading-warning" role="status" data-gradescope-replacing-note>This send replaces ${h.e(count(replacing, "grade"))} someone else made on Gradescope (listed above). Sending asks you to confirm.</p>` : "";
    const missing = kind === "written" ? sections.filter(s => !s.left_out && s.course_id && !s.container_id) : [];
    // A chosen version set lacking a version this section's papers are in (a makeup's Version E, 2026-10-06): Faculty can add it there.
    const lacking = kind === "written" ? sections.filter(s => !s.left_out && s.container_id && s.missing_versions?.length) : [];
    const letters = [...new Set(lacking.flatMap(s => s.missing_versions))].sort();
    const versionWords = `${letters.length === 1 ? "Version" : "Versions"} ${letters.join(", ")}`;
    const offerLines = [missing.length ? `<p>${missing.length === 1 ? `Section ${h.e(missing[0].section)}'s Gradescope course has` : `${h.e(missing.length)} sections' Gradescope courses have`} no assignment for this yet? Faculty can make it there, with its questions and rubrics.</p>` : "",
      lacking.length ? `<p>${lacking.length === 1 ? `Section ${h.e(lacking[0].section)} has papers` : `${h.e(lacking.length)} sections have papers`} in ${h.e(versionWords)}, which ${lacking.length === 1 ? `“${h.e(lacking[0].container)}” lacks` : "their Gradescope assignments lack"}. Faculty can add it there, with its questions and rubric.</p>` : ""].join("");
    const offer = offerLines ? `<div class="callout" data-gradescope-create-offer>${offerLines}<div class="grading-actions">${h.button("gradescope-create-offer", missing.length ? "Create the assignment on Gradescope…" : `Add ${versionWords} on Gradescope…`)}</div></div>` : "";
    outlook.innerHTML = `${notice}${offer}${result.running ? '<p class="grading-warning" role="status">A send to Gradescope is still running for this assignment. Let it finish, or cancel it on Certify, before sending again.</p>' : ""}
      ${summaryMarkup(result, h)}${questionsMarkup(result, plan, h)}${listsMarkup(result, plan, h)}
      ${withSheets ? `<div data-gradescope-sheets-slot>${sheetsPartMarkup(sheets, sheetsWanted, h)}</div>` : ""}
      ${nothing ? `<p class="grading-gradescope-empty" role="status">${h.e(words.nothing)}</p>`
        : `<div class="grading-gradescope-confirm">${publishedNote}${replacingNote}${h.check(confirmWords(result), "understood")}</div>`}`;
  }

  // What the tick confirms: the written part when it has anything, the bubble sheets when they go too.
  function confirmWords(result) {
    if (!sheetsGo()) return result.papers_only ? words.papersConfirm : words.confirm;
    if (!sendable(result)) return KINDS.sheets.confirm;
    return `${(result.papers_only ? words.papersConfirm : words.confirm).replace(/; nothing is published\.$/, "")}, after uploading the bubble sheets; nothing is published.`;
  }

  function uncertain() {
    error.textContent = "Faculty could not confirm whether this send was recorded. Retry request asks the server again with the same request ID: it returns the send already recorded, or records it once. It never starts a second send.";
    error.hidden = false;
  }

  async function deliver(record) {
    sending = true;
    unresolved.set(key, record);
    controls();
    const clear = () => { if (unresolved.get(key)?.payload.request_id === record.payload.request_id) unresolved.delete(key); };
    let receipt;
    try {
      receipt = await h.api(`${base(plan, record.kind || kind)}/send`, "POST", record.payload, {}, WAIT);
    } catch (cause) {
      const status = Number(cause.status || 0);
      if (status >= 400 && status < 500) {
        // A definite refusal (a changed preview is 409): nothing was recorded. The preview is read again, under a new request
        // ID, and sending needs another click.
        clear();
        sending = false;
        if (active()) void loadPreview(`<p class="grading-warning" role="status">${h.e(cause.message)} Nothing was sent. Check the new preview before sending again.</p>`);
        return;
      }
      if (active()) uncertain();  // no answer, an unreadable answer or a server failure: the outcome is unknown
      return;
    } finally {
      sending = false;
    }
    if (!receipt || receipt.request_id !== record.payload.request_id || typeof receipt.send_id !== "string") {
      if (active()) uncertain();
      return;
    }
    clear();
    if (active()) h.close();
    const sheetsToo = receipt.sheets ? " The bubble sheets go up first." : "";
    h.toast(receipt.status === "cancelled" ? "That send was already recorded and cancelled." : record.kind === "sheets" ? KINDS.sheets.sent
      : `${receipt.plan?.papers_only ? words.papersSent : words.sent}${sheetsToo}`);
    onSent?.();
  }

  async function send() {
    if (blocked()) throw new Error(blocked());
    if (!form.elements.understood?.checked) throw new Error("Tick the box to confirm you checked the preview.");
    const shown = preview, asked = previewTargets, id = requestId, published = sendable(shown) ? shown.published || [] : [];
    const sheetPart = sheetsGo() ? sheets : null, sheetPublished = sheetPart?.published || [];
    // Grades already published on Gradescope: one more question, in its own popup, before anything is sent there.
    if (published.length && !await confirmPublished(published, h, kind, form.closest("dialog"))) return;
    if (!active() || preview !== shown) return;  // the dialog closed or the preview changed while the popup was open
    if (sheetPublished.length && !await confirmPublished(sheetPublished, h, "sheets", form.closest("dialog"))) return;
    if (!active() || preview !== shown) return;
    const sheetsPayload = sheetPart ? {fingerprint: sheetPart.fingerprint, targets: sheetPart.targets || {},
      ...(sheetPublished.length ? {published: [...new Set(sheetPublished.map(p => String(p.container_id)))]} : {})} : null;
    if (!sendable(shown)) {
      // Only the bubble sheets have anything to send: the Bubble sheets page's own send, as that page would make it.
      await deliver({kind: "sheets", payload: {request_id: id, confirmation: "send", ...sheetsPayload}, preview: shown, targets: asked, sheets: sheetPart});
      return;
    }
    // Grades someone else made on Gradescope: the same, for the grades this send replaces.
    const replacing = shown.replace_others ? Number(shown.replacing?.grades || 0) : 0;
    if (replacing && !await confirmReplacing(shown.replacing, h, form.closest("dialog"))) return;
    if (!active() || preview !== shown) return;
    // The server's own resolution (preview.targets) for every section that has its exam, and what was asked for the others, so the
    // send is read exactly as the preview was.
    const targets = {...asked, ...(shown.targets || {})};
    const confirmed = [...new Set(published.map(p => String(p.container_id)))];
    // The preview's own mode, never the radio's: a changed choice reloads the preview before it can be sent.
    await deliver({payload: {request_id: id, fingerprint: shown.fingerprint, confirmation: "send", targets, ...(confirmed.length ? {published: confirmed} : {}),
      ...(shown.papers_only ? {papers_only: true} : {}),
      // The replacements exactly as the preview showed them: the server plans them from these instead of reading Gradescope again.
      ...(shown.replace_others ? {replace_others: true, replacing, ...(Array.isArray(shown.replacements) ? {replacements: shown.replacements} : {})} : {}),
      // The bubble sheets of the preview shown, recorded with this send and before it (THE BUBBLE SHEETS TOO).
      ...(sheetsPayload ? {sheets: sheetsPayload} : {})},
      preview: shown, targets: asked, sheets: sheetPart});
  }

  form.onsubmit = event => { event.preventDefault(); void runFormAction(form, send).then(finish); };
  // The send dialog's own choices go along, so the create preview covers the same sections and courses.
  outlook.addEventListener("click", event => {
    if (event.target.closest('[data-grading-action="gradescope-create-offer"]') && !sending) createDialog(plan, h, onSent, JSON.parse(JSON.stringify(choices)));
  });
  retry.onclick = () => { const record = unresolved.get(key); if (record) void runFormAction(form, () => deliver(record)).then(finish); };
  check.onclick = () => { if (!loading && !sending) void loadPreview(); };
  form.querySelector("[data-gradescope-mode]")?.addEventListener("change", event => {
    if (event.target.name !== "gradescope-what" || sending || unresolved.has(key)) return;
    only = event.target.value === "papers";
    if (only) replace = false;  // papers only replaces nothing; going back to grades starts with the choice off again
    label();
    drawReplace();
    void loadPreview();
  });
  outlook.addEventListener("change", event => {
    if (event.target.name !== "gradescope-sheets" || sending || unresolved.has(key) || !preview) return;
    sheetsWanted = event.target.checked;
    draw(preview);  // the confirmation says what goes, so it is asked again
    controls();
    form.querySelector("[name='gradescope-sheets']")?.focus();
  });
  replaceSlot?.addEventListener("change", event => {
    if (event.target.name !== "gradescope-replace" || sending || unresolved.has(key)) return;
    replace = event.target.checked;
    void loadPreview();
  });
  targetsSlot.addEventListener("click", event => {
    const button = event.target.closest?.("[data-gradescope-change]");
    if (!button || button.disabled || !preview) return;
    changing.add(button.dataset.gradescopeChange);
    draw(preview);
    controls();
    form.querySelector(`[data-gradescope-course="${CSS.escape(button.dataset.gradescopeChange)}"]`)?.focus();
  });
  targetsSlot.addEventListener("change", event => {
    const select = event.target;
    if (select instanceof HTMLInputElement && select.dataset.gradescopeLeave !== undefined) {
      const id = select.dataset.gradescopeLeave, kept = {...(choices[id] || {})};
      delete kept.leave_out;
      if (select.checked) choices[id] = {...kept, leave_out: true};
      else if (kept.course_id) choices[id] = kept;
      else delete choices[id];
      void loadPreview();
      return;
    }
    if (!(select instanceof HTMLSelectElement)) return;
    if (select.dataset.gradescopeCourse !== undefined) {
      const id = select.dataset.gradescopeCourse;
      if (select.value) choices[id] = {course_id: select.value, container_id: ""};
      else delete choices[id];
      const exams = form.querySelector(`[data-gradescope-container="${CSS.escape(id)}"]`);
      if (exams) exams.innerHTML = '<option value="">Reading this course’s exams…</option>';
    } else if (select.dataset.gradescopeContainer !== undefined) {
      const id = select.dataset.gradescopeContainer;
      choices[id] = {course_id: choices[id]?.course_id || "", container_id: select.value};
    } else return;
    void loadPreview();
  });

  const previous = unresolved.get(key);
  if (previous) {
    // The earlier request's outcome is unknown: show what it asked for and offer only its explicit retry.
    preview = previous.preview; previewTargets = previous.targets; requestId = previous.payload.request_id;
    choices = JSON.parse(JSON.stringify(previous.payload.targets || {}));
    only = Boolean(previous.payload.papers_only);  // the request as it was asked, whichever button opened the dialog
    replace = Boolean(previous.payload.replace_others);
    sheets = previous.sheets || {};  // the bubble sheets the request named, if any: shown as they were, never read again here
    sheetsWanted = Boolean(previous.sheets);
    form.querySelectorAll("[name='gradescope-what']").forEach(radio => { radio.checked = (radio.value === "papers") === only; });
    label();
    drawReplace();
    draw(previous.preview, '<p class="grading-warning" role="status">An earlier send request did not get an answer. This is the preview it named.</p>');
    uncertain();
    controls();
  } else void loadPreview();
  return form;
}
