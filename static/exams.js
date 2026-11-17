import { createAssignmentTemplates } from "./assignment-templates.js";
import { setUpGradingNotice } from "./assignment-wizard.js";
import { pipelineMarkup } from "./grading-guide.js";
import { patchChildren } from "./dom-patch.js";
import { calloutMarkup, daysFrom, disclosureMarkup, emptyMarkup, loadingMarkup, nextStepMarkup, registerLeaveGuard, stateLabel, wallDate, captureDialogIntent, dialogSession } from "./ui.js";
import { viewLink } from "./file-viewer.js";

// The tab "grading" keeps its key (links and tests use ?tab=grading); it is the Exams page's own manual scoring, named so beside
// assisted grading on the Grading page (UX overhaul, 2026-09-26).
const EXAM_TABS = [["overview", "Overview"], ["sittings", "Sittings & students"], ["collection", "Collection"], ["changes", "Changes"], ["grading", "Manual scores"], ["requests", "Requests"]];
// Collection states that need nothing more from the instructor; every other row is attention (or a recorded absence).
const SETTLED = new Set(["complete", "absent", "excused"]);
const STAGE_STATES = {done: "Done", failed: "Failed", started: "In progress", idle: "Not started"};
const STAGE_LINKS = {paper: "Assignments", seating: "Seating", notices: "Response Cards", print: "Print jobs", exam_day: "Exam day",
  collected: "Collection", grading: "Grading", recorded: "Gradebook"};
// A tile's link is named by where it goes: the projection sends a stage to the Teaching Inbox until its owner has a record.
const LINK_NAMES = [["#inbox", "Teaching Inbox"], ["#accommodations", "DR exam schedule"], ["#response-cards", "Response Cards"], ["#seating", "Seating"],
  ["#gradebook", "Gradebook"], ["#grading", "Grading"]];
const EXPECTATIONS = {expected: "Expected", absent: "Absent", excused: "Excused"};
const LEAVE_QUESTION = "Leave these unsaved grading changes?";
const REQUEST_KINDS = {seat: "Seat problem", schedule: "Schedule conflict", notice: "Incorrect notice", makeup: "Makeup request", other: "Other"};
const BADGE_TONES = Object.fromEntries([
  ...["absent", "missing", "not_received", "conflict", "incomplete", "partial", "issue", "needs_review", "open", "stale"].map((key) => [key, "warning"]),
  ...["complete", "reviewed", "released", "resolved", "approved", "finalized"].map((key) => [key, "success"]),
  ["declined", "danger"],
]);
const REQUEST_STATES = {open: "Open", in_review: "In review", approved: "Approved", declined: "Declined", resolved: "Resolved"};

// The exam is a term-owned use of an existing course assignment. Student records
// continue to reference permanent section/enrollment memberships.
export function createExams({api, escape, toast, showDialog, dialogHeader, header = null}) {
  const base = "/api/exams";
  const root = () => document.querySelector("#exams-section");
  const e = (value) => escape(String(value ?? ""));
  const enc = encodeURIComponent;
  const pretty = (value) => String(value || "Unknown").replaceAll("_", " ");
  const option = (value, label, selected = "") => `<option value="${e(value)}" ${String(value) === String(selected) ? "selected" : ""}>${e(label)}</option>`;
  const when = (value) => value ? new Date(value).toLocaleString() : "Not yet";
  const requestId = () => crypto.randomUUID();
  const errorLine = () => '<p class="form-error" role="alert" hidden></p>';
  const action = (key, text, attrs = "", primary = false) => `<button type="button" class="button${primary ? " primary" : ""}" data-exam-action="${key}" ${attrs}>${e(text)}</button>`;
  // A record's state as a shared chip (components.css); the tone says whether it needs the instructor.
  const badge = (status) => {
    const key = String(status || "unknown").replace(/[^a-z_]/g, "");
    const tone = BADGE_TONES[key];
    return `<span class="chip exams-chip" data-state="${e(key)}"${tone ? ` data-tone="${tone}"` : ""}>${e(pretty(status))}</span>`;
  };
  const membership = (item) => `${item.section_id}/${item.enrollment_id}`;
  const issueLink = (id, title = "Open response cards") => `<a class="button" href="#response-cards/issue/${enc(id)}">${e(title)}</a>`;
  let catalog = null, exam = null, grading = null, requests = null;
  // The lifecycle hub (GET /api/exams/<id>/overview or /lifecycle): a read-only projection; `hubText` is its last JSON, so a poll
  // that finds nothing new changes nothing. `assessment` is set on #exams/assessment/<assignment>/<term> (no exam record yet).
  let hub = null, hubText = "", assessment = null, hubGeneration = 0;
  let route = "", tab = "overview", generation = 0, tabGeneration = 0, workGeneration = 0;
  let pageReady = false, tabReady = false;
  // The full load under way (its address and generation). The shell renders both on a navigation and when its state read returns:
  // an unforced render of the same address joins that load instead of starting the same reads again.
  let loading = null;
  let selectedStudent = "", selectedQuestion = "", selectedDocument = "", anonymous = true, work = null;
  let rubricDraft = null, rubricDirty = false, markDirty = false;
  const templates = createAssignmentTemplates({api, escape, toast, showDialog, dialogHeader});

  function termLabel(item) {
    return item.term_label || item.label || `${item.term === "Other" ? item.term_other : item.term || ""} ${item.year || ""}`.trim();
  }
  function sectionLabel(item) { return typeof item === "object" ? item.number ?? item.section_number : item; }
  function studentName(item, index = 0) { return anonymous ? `Paper ${index + 1} · Section ${item.section_number ?? "—"}` : `${item.name || "Unnamed student"} · Section ${item.section_number ?? "—"}`; }
  const studentOptions = (list) => list.map((item, index) => option(membership(item), `${studentName(item, index)} · ${pretty(item.status || "ungraded")}`, selectedStudent)).join("");
  function path(suffix = "") { return `${base}/${enc(exam.id)}${suffix}`; }
  function currentRoute() { return location.hash.split("?")[0]; }
  function closeDialog() { document.querySelector("#dialog")?.close(); }
  function alertMessage(error) { toast(error.message || String(error), true); }
  function dirtyLeave() { return !(rubricDirty || markDirty) || window.confirm(LEAVE_QUESTION); }

  async function perform(form, operation) {
    const target = currentRoute(), owner = exam;
    const dialog = document.querySelector("#dialog");
    const active = dialog.contains(form) ? dialogSession(form, dialog) : () => form.isConnected;
    const current = () => active() && currentRoute() === target && exam === owner;
    if (!current()) return;
    if (form.dataset.saving === "true") return;
    const controls = [...form.querySelectorAll("button, input, select, textarea")];
    const prior = controls.map((item) => item.disabled);
    const error = form.querySelector('[role="alert"]');
    if (error) { error.hidden = true; error.textContent = ""; }
    form.dataset.saving = "true";
    controls.forEach((item) => { item.disabled = true; });
    try { await operation(current); }
    catch (cause) {
      if (error && form.isConnected) { error.textContent = cause.message; error.hidden = false; }
      else alertMessage(cause);
    } finally {
      delete form.dataset.saving;
      controls.forEach((item, index) => { item.disabled = prior[index]; });
    }
  }
  async function run(button, operation) {
    if (button.disabled) return;
    button.disabled = true;
    try { await operation(); } catch (cause) { alertMessage(cause); }
    finally { button.disabled = false; }
  }
  function formDialog(title, subtitle, body, submitText, onSubmit) {
    showDialog(`<form id="exams-dialog">${dialogHeader(e(title), e(subtitle))}<div class="dialog-body exams-dialog-body">${body}${errorLine()}</div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${e(submitText)}</button></footer></form>`);
    const form = document.querySelector("#exams-dialog");
    const owner = exam, target = currentRoute();
    const active = dialogSession(form, document.querySelector("#dialog"));
    const current = () => active() && exam === owner && currentRoute() === target;
    form.onsubmit = (event) => {
      event.preventDefault();
      const values = new FormData(form);
      if (current()) void perform(form, () => onSubmit(values, form, current));
    };
    return form;
  }

  function queryTab() {
    const value = new URLSearchParams(location.hash.split("?")[1] || "").get("tab");
    return EXAM_TABS.some(([key]) => key === value) ? value : "";
  }
  /** The address names the open tab, so Back, a reload and links such as ?tab=collection land on it. */
  function rememberTab() {
    const target = `${route}${tab === "overview" ? "" : `?tab=${tab}`}`;
    if (location.hash !== target && currentRoute() === route) history.replaceState(history.state, "", target);
  }
  function sectionsText(numbers) { return numbers?.length ? `Section${numbers.length === 1 ? "" : "s"} ${numbers.join(", ")}` : ""; }
  /** What a lifecycle with no next step says, the same on the hub and its list card. */
  function restingTitle(phase) { return phase === "upcoming" ? "Ready for the day" : phase === "past" ? "Nothing left to do" : "Nothing to do yet"; }
  function whenLine(dates) {
    if (!dates?.date) return "";
    const day = daysFrom(dates.date, dates.today);
    return [wallDate(dates.date), dates.time, (dates.rooms || []).join(", "), day].filter(Boolean).join(" · ");
  }

  async function render(force = false) {
    const target = currentRoute();
    if (!root() || !target.startsWith("#exams")) return;
    if (!force && loading?.hash === location.hash && loading.generation === generation) {
      // A return while that load is still under way: the shell put its default header back, so name the exam without reading again.
      if (pageReady && (exam || assessment)) setHeader();
      return;
    }
    const wanted = queryTab();
    // The app refreshes every 30 seconds. Keep forms, selection and scan position
    // intact until the instructor changes route or explicitly refreshes this view.
    if (!force && target === route && root().childElementCount && pageReady) {
      // The shell puts its default header back whenever the route changes; a return to this page names the exam again.
      if (exam || assessment) setHeader();
      if (exam && wanted && wanted !== tab) {
        // A link to another tab of the exam on screen (a hub tile, ?tab=collection). The shell's leave guard already asked
        // about unsaved grading changes before this hash change reached the page.
        rubricDirty = false; markDirty = false; tab = wanted; updateTabs(); void renderTab();
      } else if (exam && !tabReady) await renderTab();
      else if (exam && tab === "grading" && root().querySelector("#exam-grading-work > .loading-state")) await loadWork();
      else if ((exam || assessment) && tab === "overview") await refreshHub();
      return;
    }
    const token = ++generation;
    const load = loading = {hash: location.hash, generation: token};
    ++tabGeneration; ++workGeneration; ++hubGeneration;
    if (target !== route) { tab = wanted || "overview"; rubricDirty = false; markDirty = false; selectedStudent = ""; selectedQuestion = ""; selectedDocument = ""; }
    route = target;
    pageReady = false;
    hub = null; hubText = "";
    root().innerHTML = loadingMarkup("Loading exams…");
    try {
      const match = target.match(/^#exams\/([^/]+)$/);
      const pair = target.match(/^#exams\/assessment\/([^/]+)\/([^/]+)$/);
      const id = match ? decodeURIComponent(match[1]) : "";
      if (pair) {
        const query = new URLSearchParams({assignment_id: decodeURIComponent(pair[1]), term_id: decodeURIComponent(pair[2])});
        const result = await api(`${base}/lifecycle?${query}`);
        if (token !== generation || root().hidden || currentRoute() !== target) return;
        exam = null; assessment = result;
        // An exam record made since this address was saved: its own page holds everything.
        if (result.event_id) { location.replace(`#exams/${enc(result.event_id)}`); return; }
        setHub(result); renderAssessment(); pageReady = true; tabReady = true;
        return;
      }
      const [context, result, overview] = await Promise.all([api(`${base}/catalog`), api(id ? `${base}/${enc(id)}` : base),
        id ? api(`${base}/${enc(id)}/overview`).catch(() => null) : null]);
      if (token !== generation || root().hidden || currentRoute() !== target) return;
      catalog = context; assessment = null;
      if (id) { exam = result; setHub(overview); renderShell(); pageReady = true; await renderTab(); }
      else { exam = null; renderList(result); pageReady = true; }
    } catch (cause) {
      if (token !== generation) return;
      root().innerHTML = `<div class="empty-state"><h3>Exams unavailable</h3><p role="alert">${e(cause.message)}</p>${action("refresh", "Try again")}</div>`;
      pageReady = true; tabReady = true;
      bindActions(root());
    } finally {
      if (loading === load) loading = null;
    }
  }

  let hubAt = 0;
  function setHub(result) { hub = result; hubText = result ? JSON.stringify(result) : ""; hubAt = Date.now(); }
  /** A poll on the hub: redraw in place only when the projection changed. The shell's first refresh right after the page read the
   * hub (within a few seconds) reads nothing again. */
  async function refreshHub() {
    if (Date.now() - hubAt < 5000) return;
    const token = ++hubGeneration, target = route;
    const url = exam ? path("/overview") : `${base}/lifecycle?${new URLSearchParams({assignment_id: assessment.assignment_id, term_id: assessment.term_id})}`;
    let result;
    try { result = await api(url); } catch { return; }
    if (token !== hubGeneration || currentRoute() !== target || tab !== "overview" || JSON.stringify(result) === hubText) return;
    setHub(result);
    setHeader();
    const panel = root().querySelector(exam ? "#exam-panel" : "#exam-hub");
    if (panel) { patchChildren(panel, overview()); bindActions(panel); }
  }

  function setHeader() {
    if (!header) return;
    const subject = exam || assessment;
    if (!subject) {
      header.set({route: location.hash, title: "Exams", subtitle: "Each assignment’s paper, seating, notices, collection and grading in one place."});
      return;
    }
    const next = hub?.next;
    const secondary = exam
      ? [{label: "Edit title", run: () => renameExam()}, {label: "Refresh", run: () => { if (dirtyLeave()) { rubricDirty = false; markDirty = false; void render(true); } }}]
      : [{label: "Create exam record", run: () => void openCreate({termId: assessment.term_id, assignmentId: assessment.assignment_id})}];
    // ASSIGNMENT-WORKSPACE titles the page with the assignment, so a record named apart from it says its own name (the one Edit
    // title changes) at the head of the subtitle.
    const named = exam && exam.assignment_title && exam.title !== exam.assignment_title ? `Exam record: ${exam.title}` : "";
    header.set({
      route: location.hash,
      title: subject.title,
      context: [subject.code, termLabel(subject), sectionsText(exam ? (exam.sections || []).map(sectionLabel) : assessment.sections)].filter(Boolean).join(" · "),
      subtitle: [named, whenLine(hub?.dates) || (hub ? "No date recorded" : "")].filter(Boolean).join(" · "),
      crumbs: [{label: "Home", href: "#overview"}, {label: "Exams", href: "#exams"}, {label: subject.title}],
      // One primary per screen: on the Overview the next-step callout carries it; on the other tabs the header does, named by
      // the step itself, and not at all on the tab the step already is.
      action: next && !(tab === "overview" || assessment || onThisTab(next.href)) ? {label: next.title, icon: "arrow-right", run: () => { location.hash = next.href; }} : null,
      secondary,
    });
  }

  function onThisTab(href) {
    const [target, search = ""] = String(href || "").split("?");
    return Boolean(exam) && target === `#exams/${enc(exam.id)}` && (new URLSearchParams(search).get("tab") || "overview") === tab;
  }

  function renderList(result) {
    const items = [...(result.exams || []).map((item) => ({...item.summary || {}, event: item, key: item.id})),
      ...(result.assessments || []).map((item) => ({...item, event: null, key: `${item.assignment_id}/${item.term_id}`}))];
    setHeader();
    if (!items.length) {
      root().innerHTML = emptyMarkup({icon: "calendar", title: "No exams yet", body: "Choose a teaching term and the course assignment you’ve prepared.", action: {label: "Create an exam", data: {examAction: "create"}}});
      bindActions(root());
      return;
    }
    const upcoming = items.filter((item) => item.phase === "upcoming").sort((a, b) => String(a.dates.date).localeCompare(String(b.dates.date)));
    // Archived (a view, certified_archive.py): an exam record (any assignment kind) whose grades are certified, and still current, with nothing hard left to do.
    const certified = (item) => (item.stages || []).some((stage) => stage.key === "grading" && stage.counts?.certification === "current");
    const archived = items.filter((item) => item.phase !== "upcoming" && certified(item) && (!item.next || item.next.soft));
    // Past exams with nothing left but the suggestion to record them (no gradebook column yet) are earlier exams too.
    const earlier = items.filter((item) => !archived.includes(item) && item.phase === "past" && item.stages && (!item.next || item.next.soft));
    const current = items.filter((item) => !upcoming.includes(item) && !earlier.includes(item) && !archived.includes(item));
    const group = (title, list) => list.length ? `<section class="exams-group" aria-label="${e(title)}"><h2 class="exams-group-title">${e(title)}</h2><div class="exams-cards">${list.map(examCard).join("")}</div></section>` : "";
    root().innerHTML = `${group("Coming up", upcoming)}${group("In progress", current)}${earlier.length
      ? disclosureMarkup({label: "Earlier exams", hint: earlier.length, key: "exams:earlier", className: "exams-earlier", body: `<div class="exams-cards">${earlier.map(examCard).join("")}</div>`}) : ""}${archived.length
      ? disclosureMarkup({label: "Archived · grades certified", hint: archived.length, key: "exams:archived", className: "exams-earlier exams-archived", body: `<div class="exams-cards">${archived.map(examCard).join("")}</div>`}) : ""}`;
    bindActions(root());
  }
  function examCard(item) {
    const event = item.event;
    const href = event ? `#exams/${enc(event.id)}` : `#exams/assessment/${enc(item.assignment_id)}/${enc(item.term_id)}`;
    const title = event ? event.title : item.title;
    const sections = event ? (event.sections || []).map(sectionLabel) : item.sections || [];
    const context = [event?.code || item.code, termLabel(event || item), sectionsText(sections)].filter(Boolean).join(" · ");
    const stages = Object.fromEntries((item.stages || []).map((stage) => [stage.key, stage]));
    const facts = ["collected", "grading", "notices"].map((key) => stages[key]).filter((stage) => stage && stage.state !== "idle");
    const shown = facts.length ? facts : ["print", "seating"].map((key) => stages[key]).filter((stage) => stage && stage.state !== "idle");
    const next = item.next;
    const foot = next
      ? `<span class="exams-card-next">${e(next.title)}</span><a class="button" href="${e(next.href)}">${e(next.label)} →</a>`
      : `<span class="exams-card-next">${item.stages ? e(restingTitle(item.phase)) : `${e((event?.participant_count ?? 0).toLocaleString())} students`}</span>`;
    return `<article class="card card--link exams-card" data-href="${e(href)}"><header class="card-head"><div><p class="eyebrow">${e(context)}</p><h3><a href="${e(href)}">${e(title)}</a></h3></div>${event ? "" : '<span class="chip" data-tone="neutral">No exam record</span>'}</header>
      <div class="card-body">${item.dates?.date ? `<p class="exams-card-when">${e(whenLine(item.dates))}</p>` : ""}${shown.length ? `<ul class="exams-card-facts">${shown.map((stage) => `<li><span>${e(stage.label)}</span> ${e(stage.detail)}</li>`).join("")}</ul>` : ""}</div>
      <footer class="card-foot">${foot}</footer></article>`;
  }

  function renderShell() {
    const attention = Object.entries(exam.reconciliation?.counts || {}).reduce((sum, [status, count]) => sum + (SETTLED.has(status) ? 0 : count), 0)
      + (exam.reconciliation?.unassigned_received_count || 0);
    // The hub's own verdict first: before any paper is seen (Collected idle) a student without a scan is no work yet, so the tab
    // carries no count; once papers arrive it counts what the Collection tab's own "need attention" line counts.
    const unseen = (hub?.stages || []).find((stage) => stage.key === "collected")?.state === "idle";
    const counts = {sittings: (exam.sittings || []).length, collection: unseen ? 0 : attention, changes: exam.impacts?.count || 0, requests: exam.request_counts?.open || 0};
    root().innerHTML = `<a class="back-link exams-back" href="#exams">← All exams</a>
      <div class="exams-tabs" role="tablist" aria-label="Exam workspace" data-exam-subtabs>${EXAM_TABS.map(([key, label]) => `<button type="button" role="tab" id="exam-tab-${key}" aria-controls="exam-panel" aria-label="${e(label)}" aria-selected="${key === tab}" tabindex="${key === tab ? "0" : "-1"}" data-exam-tab="${key}">${e(label)}${counts[key] ? ` <span class="exams-tab-count" aria-hidden="true">${e(counts[key].toLocaleString())}</span>` : ""}</button>`).join("")}</div><section id="exam-panel" class="exams-panel" role="tabpanel" aria-labelledby="exam-tab-${tab}"></section>`;
    setHeader();
    root().querySelectorAll("[data-exam-tab]").forEach((button) => {
      button.onclick = () => {
        if (button.dataset.examTab === tab || !dirtyLeave()) return;
        rubricDirty = false; markDirty = false; tab = button.dataset.examTab;
        updateTabs(); void renderTab();
      };
      button.onkeydown = (event) => {
        const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
        if (!keys.includes(event.key)) return;
        event.preventDefault();
        const tabs = [...root().querySelectorAll("[data-exam-tab]")], index = tabs.indexOf(button);
        const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
        tabs[next].focus(); tabs[next].click();
      };
    });
    bindActions(root());
  }
  function renderAssessment() {
    root().innerHTML = `<a class="back-link exams-back" href="#exams">← All exams</a><section id="exam-hub" class="exams-panel">${overview()}</section>`;
    centreGraph(root());
    setHeader();
    bindActions(root());
  }
  function updateTabs() {
    setHeader();
    root().querySelectorAll("[data-exam-tab]").forEach((button) => {
      const selected = button.dataset.examTab === tab;
      button.setAttribute("aria-selected", String(selected)); button.tabIndex = selected ? 0 : -1;
    });
    root().querySelector("#exam-panel").setAttribute("aria-labelledby", `exam-tab-${tab}`);
    rememberTab();
  }
  async function reload() {
    const id = exam.id, target = route;
    const [result, overview] = await Promise.all([api(path()), api(path("/overview")).catch(() => null)]);
    if (currentRoute() !== target || exam?.id !== id) return;
    exam = result; setHub(overview); renderShell(); await renderTab();
  }
  async function renderTab() {
    tabReady = false;
    const token = ++tabGeneration, eventId = exam.id;
    ++workGeneration;
    const panel = root().querySelector("#exam-panel");
    panel.innerHTML = loadingMarkup("Loading…");
    try {
      if (tab === "grading") {
        const result = await api(path(`/grading${selectedDocument ? `?assignment_version_id=${enc(selectedDocument)}` : ""}`));
        if (token !== tabGeneration || exam?.id !== eventId || root().hidden) return;
        grading = result; renderGrading(panel);
      } else if (tab === "requests") {
        const result = await api(path("/requests"));
        if (token !== tabGeneration || exam?.id !== eventId || root().hidden) return;
        requests = result; renderRequests(panel);
      } else if (tab === "overview") { panel.innerHTML = overview(); centreGraph(panel); }
      else if (tab === "sittings") panel.innerHTML = sittings();
      else if (tab === "collection") panel.innerHTML = collection();
      else if (tab === "changes") panel.innerHTML = changes();
      bindActions(panel); bindFilters(panel);
      tabReady = true;
    } catch (cause) {
      if (token !== tabGeneration) return;
      panel.innerHTML = `<div class="callout exams-notice" data-tone="warning" role="alert"><p>${e(cause.message)}</p>${action("tab-retry", "Try again")}</div>`;
      tabReady = true;
      bindActions(panel);
    }
  }

  /** The lifecycle hub: the step graph of the eight stages, the one next step, the pre-exam checklist and a tile per stage. */
  function overview() {
    if (!hub) return calloutMarkup({tone: "warning", title: "The exam’s lifecycle could not be read.", body: "Refresh to try again; its tabs still work.", action: {label: "Refresh", data: {examAction: "refresh"}}});
    const next = hub.next;
    const steps = hub.stages.map((stage) => ({key: stage.key, label: stage.label, title: stage.label, detail: stage.detail, status: STAGE_STATES[stage.state] || stateLabel(stage.state),
      done: stage.state === "done", failed: stage.state === "failed", started: stage.state === "started", current: stage.key === next?.stage,
      destination: stage.href || "#exams"}));
    // I6 (2026-10-18): the graph and the one next step are this record's own navigation, marked `data-exam-lifecycle` so the assignment
    // shell hides them there (its tabs and Readiness say the same); outside the shell nothing changes.
    const graph = `<div class="exams-lifecycle" data-exam-lifecycle>${pipelineMarkup(steps, {e}, null, "Assignment lifecycle")}</div>`;
    const lead = (next ? nextStepMarkup({title: next.title, detail: next.detail, label: next.label, href: next.href})
      : calloutMarkup({tone: "success", title: restingTitle(hub.phase),
        body: hub.phase === "upcoming" ? `Everything checked below is in order${hub.dates?.date ? ` for ${wallDate(hub.dates.date)}` : ""}.` : ""}))
      .replace(/^(\s*<[a-z]+)/, "$1 data-exam-lifecycle");
    const readiness = hub.readiness?.length ? `<section class="exams-readiness" aria-labelledby="exams-readiness-title"><h3 id="exams-readiness-title">Before the day</h3><ul>${hub.readiness.map((item) =>
      `<li data-ok="${item.ok}"><span class="exams-readiness-mark" aria-hidden="true">${item.ok ? "✓" : "!"}</span><a href="${e(item.href)}">${e(item.label)}</a><span class="sr-only">${item.ok ? " (ready)" : " (needs attention)"}</span></li>`).join("")}</ul></section>` : "";
    return `${graph}${lead}${readiness}<div class="exams-stage-tiles">${hub.stages.map(stageTile).join("")}</div>`;
  }
  /** Bring the current stage into view when the graph is wider than its column, as Grading does (CSS scroll-initial-target is not honoured
   * everywhere). Only on a fresh render: a quiet refresh patches in place and keeps the scroll the instructor left. */
  function centreGraph(host) {
    const scroller = host.querySelector(".exams-lifecycle .grading-pipeline-scroll"), step = scroller?.querySelector('[aria-current="step"]');
    if (!scroller || !step || scroller.scrollWidth <= scroller.clientWidth) return;
    const item = step.getBoundingClientRect(), frame = scroller.getBoundingClientRect();
    scroller.scrollLeft += item.left - frame.left - (scroller.clientWidth - item.width) / 2;
  }
  function stageTile(stage) {
    let extra = "";
    if (stage.key === "seating" && exam) {
      // Seat-change requests move students on the charts linked to this exam record (exam_workflow/requests.py), so charts found
      // for it but not linked say why they matter; linking anything else by hand stays in the folded records.
      const found = exam.available_charts || [];
      const linkFound = found.length && !(exam.charts || []).length
        ? `<p>${found.length === 1 ? "A seating chart for this exam is not linked to its record; seat-change requests need it."
          : `${found.length} seating charts for this exam are not linked to its record; seat-change requests need them.`}</p><div class="exams-actions">${action("link-found-charts", found.length === 1 ? "Link the chart" : `Link ${found.length} charts`)}</div>` : "";
      extra = `${linkFound}${records()}`;
    } else if (stage.key === "seating" && (hub.charts || []).length) {
      extra = `<ul class="exams-tile-list">${hub.charts.map((chart) => `<li><a href="#seating/${enc(chart.id)}">${e(chart.room_name)}</a> · ${chart.confirmed ? "confirmed" : "not confirmed"}</li>`).join("")}</ul>`;
    } else if (stage.key === "exam_day" && exam && (exam.sittings || []).length) {
      extra = `<ul class="exams-tile-list">${exam.sittings.map((item) => `<li>${e(item.label)} · ${e([item.date ? wallDate(item.date) : "", item.time, item.location].filter(Boolean).join(" · ") || "Schedule not set")}</li>`).join("")}</ul>`;
    } else if (stage.key === "grading" && !hub.grading_plan_id) {
      // No grading plan yet: the instructor may set grading up now, before the papers come back (requirement, 2026-10-02).
      extra = `<div class="exams-actions">${action("set-up-grading", "Set up grading now")}</div>`;
    }
    // The exam-day plan (proctors and instructions) and each room's printed packet: a link only; the hub stores nothing.
    if (stage.key === "exam_day" && (hub.charts || []).length) {
      extra += `<p class="exams-tile-link"><a class="text-button" href="#exam-day/${enc(hub.assignment_id)}/${enc(hub.term_id)}">Exam-day packet</a></p>`;
    }
    const owner = String(stage.href || "");
    const name = owner.includes("tab=sittings") ? "Sittings" : owner.includes("tab=collection") ? "Collection" : owner.includes("step=seating") ? "Seating"
      : LINK_NAMES.find(([prefix]) => owner === prefix || owner.startsWith(`${prefix}/`) || owner.startsWith(`${prefix}?`))?.[1]
        || STAGE_LINKS[stage.key] || stage.label;
    const link = stage.href ? `<a class="link-arrow" href="${e(stage.href)}">${e(name)}</a>` : "";
    return `<article class="card exams-stage" data-stage="${e(stage.key)}"><header class="card-head"><h3>${e(stage.label)}</h3></header>
      <div class="card-body"><p>${e(stage.detail)}</p>${extra}</div>${link ? `<footer class="card-foot">${link}</footer>` : ""}</article>`;
  }
  /** The exam's explicitly linked charts and print jobs, and the hand links that add one (kept on the Seating tile, folded). */
  function records() {
    const charts = (exam.charts || []).map((item) => `<div class="exams-resource"><a href="#seating/${enc(item.chart_id || item.id)}">${e(item.room_name || item.title || "Seating chart")}</a><span>${e(sittingName(item.sitting_id))}</span></div>`).join("") || '<p class="exams-empty">No seating charts linked.</p>';
    // A superseded or cancelled job stays linked to the record but prints nothing more: it says so and follows the current ones, so
    // the fold agrees with the Print tile and the Print jobs list, which count current jobs only.
    const ended = (/** @type {any} */ item) => Boolean(item.superseded_at) || item.status === "cancelled";
    const jobState = (/** @type {any} */ item) => item.superseded_at ? "Superseded" : pretty(item.status);
    const batches = [...(exam.batches || [])].sort((a, b) => Number(ended(a)) - Number(ended(b))).map((item) => `<div class="exams-resource"${ended(item) ? " data-ended" : ""}><a href="#documents?batch=${enc(item.batch_id || item.id)}">${e(batchLabel(item))}</a><span>${e(sittingName(item.sitting_id))} · ${e(jobState(item))}</span></div>`).join("") || '<p class="exams-empty">No print jobs linked.</p>';
    // The hint counts the charts and the current jobs (the Print tile's count); the ended jobs are listed after them, marked.
    const current = (exam.batches || []).filter((item) => !ended(item)).length;
    return `<details class="disclosure workflow-records"><summary><span>Charts and print jobs on this exam record</span><span class="disclosure-hint">${(exam.charts || []).length + current}</span></summary><div class="disclosure-body"><div class="exams-two-columns"><div>${charts}</div><div>${batches}</div></div><div class="exams-actions">${action("link-chart", "Link a seating chart…")}${action("link-batch", "Link a print job…")}</div></div></details>`;
  }
  function sittingName(id) { return (exam.sittings || []).find((item) => item.id === id)?.label || "No sitting assigned"; }
  // "Version A2 · Oct 1": the version and the day, as the Print jobs list names a job (a full date and time wrapped onto three lines).
  function batchLabel(item) {
    const day = item.created_at ? new Date(item.created_at).toLocaleDateString(undefined, {month: "short", day: "numeric"}) : "";
    return item.label || `${item.version_label ? `Version ${item.version_label}` : item.title || "Print job"}${item.section_number != null ? ` · Section ${item.section_number}` : ""}${day ? ` · ${day}` : ""}`;
  }
  function sittingCard(item) {
    return `<article class="exams-sitting"><div><h4>${e(item.label)} ${badge(item.kind)}</h4><p>${e([item.date, item.time, item.location || item.room_name].filter(Boolean).join(" · ") || "Schedule not set")}</p></div>${action("edit-sitting", "Edit", `data-id="${e(item.id)}"`)}</article>`;
  }
  function table(headers, rows, emptyText = "No records to show.") {
    return rows.length ? `<div class="exams-table-wrap"><table class="exams-table"><thead><tr>${headers.map((text) => `<th scope="col">${e(text)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>` : `<p class="exams-empty">${e(emptyText)}</p>`;
  }
  function studentFilter(statuses = null, {selected = "", attention = false} = {}) {
    const choices = statuses ? `${option("", "All statuses", selected)}${attention ? option("attention", "Needs attention", selected) : ""}${statuses.map((status) => option(status, pretty(status), selected)).join("")}` : "";
    return `<div class="exams-toolbar"><label class="field exams-search">Find a student<input type="search" data-student-search data-page-search placeholder="Student name or section" aria-label="Find a student"></label>${statuses ? `<label class="field">Status<select data-status-filter>${choices}</select></label>` : ""}<p class="field-hint" data-filter-count aria-live="polite"></p></div>`;
  }
  function studentRowAttrs(item, status = "") { return `data-student-row data-search="${e(`${item.name || ""} Section ${item.section_number ?? ""}`.toLocaleLowerCase())}" data-status="${e(status)}"`; }
  function sittings() {
    return `<section class="exams-block"><div class="exams-toolbar"><div><h3>Sittings</h3><p class="section-lead">Set the main sitting and any separate exam times.</p><p class="supporting-copy">Dates and times are the local values you enter.</p></div>${action("add-sitting", "Add sitting", "", true)}</div>${(exam.sittings || []).map(sittingCard).join("") || '<p class="exams-empty">No sittings yet.</p>'}</section><section class="exams-block"><div class="exams-toolbar"><div><h3>Students</h3><p class="section-lead">Record where and when each student is expected.</p><p class="supporting-copy">Record a sitting or an explicit absence. A missing scan never changes this record.</p></div>${action("sync-roster", "Add new roster students")}</div>${studentFilter(Object.keys(EXPECTATIONS))}${table(["Student", "Section", "Sitting", "Expectation", ""], (exam.participants || []).map((item) => `<tr ${studentRowAttrs(item, item.expectation)}><td>${e(item.name)}${item.reason ? `<small>${e(item.reason)}</small>` : ""}</td><td>${e(item.section_number)}</td><td>${e(sittingName(item.sitting_id))}</td><td>${badge(item.expectation)}</td><td>${action("edit-participant", "Edit", `data-key="${e(membership(item))}"`)}</td></tr>`))}</section>`;
  }
  function copyLinks(copies) {
    return (copies || []).map((copy) => {
      const jobs = copy.job_ids || (copy.job_id ? [copy.job_id] : []);
      return `<div class="exams-copy">${e(copy.label || `Copy ${copy.copy_number ?? ""}`)}${copy.missing_pages?.length ? ` · missing pages ${copy.missing_pages.map(e).join(", ")}` : ""}${jobs.map((id) => ` <a href="#inbox/job/${enc(id)}">Review scan</a>`).join("")}</div>`;
    }).join("");
  }
  function collection() {
    // One record per student, sent once (reconciliation.students).
    const rows = exam.reconciliation?.students || [];
    const statuses = [...new Set(rows.map((item) => item.status || "no_scan"))];
    const unassigned = exam.reconciliation?.unassigned_copies || [];
    const complete = rows.filter((item) => item.status === "complete").length;
    const attention = rows.filter((item) => !SETTLED.has(item.status)).length;
    // Most papers in: open on the ones that still need something, with the rest one click away.
    const focus = rows.length && attention && complete * 2 > rows.length;
    const participants = new Map((exam.participants || []).map((p) => [membership(p), p]));
    const summary = attention ? `<p class="exams-attention-line"><strong>${e(attention.toLocaleString())} of ${e(rows.length.toLocaleString())}</strong> need attention${focus ? ` · <button type="button" class="text-button" data-exam-action="show-all">Show all</button>` : ""}</p>` : "";
    return `<section class="exams-block"><h3>Account for each paper</h3><p class="section-lead">Reviewed papers, incomplete scans and unreviewed arrivals stay distinct; resolve scan choices in the Teaching Inbox.</p><div class="exams-counts">${Object.entries(exam.reconciliation?.counts || {}).filter(([, count]) => count).map(([status, count]) => `<span>${badge(status)} <strong>${e(count)}</strong></span>`).join("")}</div>${summary}${studentFilter(statuses, {selected: focus ? "attention" : "", attention: Boolean(attention)})}${table(["Student", "Section", "Expected", "Collection", "Paper"], rows.map((record) => {
      const status = record.status || "no_scan";
      const participant = participants.get(membership(record)) || record;
      return `<tr ${studentRowAttrs({...participant, ...record}, status)}><td>${e(record.name || participant.name)}</td><td>${e(participant.section_number)}</td><td>${badge(participant.expectation)}</td><td>${badge(status)}${(record.issues || []).map((issue) => `<small>${e(typeof issue === "string" ? issue : issue.message || pretty(issue.kind))}</small>`).join("")}</td><td>${record.assembly_revision_id ? `<a ${viewLink(`/api/documents/assemblies/${enc(record.assembly_revision_id)}/download`, "Reviewed paper", e)}>Reviewed paper</a>` : ""}${copyLinks(record.copies)}</td></tr>`;
    }))}</section><section class="exams-block"><h3>Copies without a student assignment</h3><p class="supporting-copy">These copies need explicit assignment before they can satisfy a student's expected paper.</p>${unassigned.length ? unassigned.map((copy) => `<div class="exams-resource">${copyLinks([copy])}<a href="#documents?batch=${enc(copy.batch_id || copy.print_batch_id)}">Open print job</a></div>`).join("") : '<p class="exams-empty">No unassigned copies in the linked print jobs.</p>'}</section>`;
  }
  function changes() {
    const impacts = exam.impacts || {}, items = impacts.changes || [];
    return `<section class="exams-block"><div class="exams-toolbar"><div><h3>Changes since your last review</h3><p class="section-lead">Catch up on what changed before your next step.</p><p class="supporting-copy">Last checkpoint: ${e(when(impacts.checkpoint_at))}.</p><div class="detail-group"><p class="supporting-copy">Reviewing changes records a baseline; it does not change seats, cards, or print artifacts. Unresolved inconsistencies remain visible after review.</p></div></div>${action("checkpoint", impacts.checkpoint_id ? "Mark these changes reviewed" : "Save first checkpoint", impacts.fingerprint ? "" : "disabled", true)}</div>${items.length ? `<ul class="exams-change-list">${items.map((item) => `<li><span class="exams-change-kind">${e(pretty(item.kind))}</span><p>${e(item.message)}</p>${changeDetails(item)}<div class="exams-actions">${[...new Set([...(item.chart_ids || []), ...(item.chart_id ? [item.chart_id] : [])])].map((id) => `<a href="#seating/${enc(id)}">Open seating chart</a>`).join("")}${[...new Set([...(item.batch_ids || []), ...(item.batch_id ? [item.batch_id] : [])])].map((id) => `<a href="#documents?batch=${enc(id)}">Open print job</a>`).join("")}${(item.issue_ids || []).map((id) => issueLink(id, "Review notices")).join("")}</div></li>`).join("")}</ul>` : '<div class="exams-empty">No changes to review at this checkpoint.</div>'}</section>`;
  }
  function changeDetails(item) {
    const labels = {label: "Label", date: "Date", time: "Time", start_time: "Start", end_time: "End", duration: "Duration", location: "Location", campus: "Campus", room_name: "Room", name: "Room name", exam_date: "Exam date", exam_time: "Exam time", broken: "Broken", blocked: "Blocked", status: "Status", expectation: "Expectation", reason: "Reason", sitting_id: "Sitting"};
    const old = {...(item.before || {}), ...(item.before?.schedule || {})}, next = {...(item.after || {}), ...(item.after?.schedule || {})};
    const value = (key, current) => current == null || current === "" ? "Not set" : key === "sitting_id" ? sittingName(current) : ["broken", "blocked"].includes(key) ? (current ? "Yes" : "No") : String(current);
    const fields = Object.keys(labels).filter((key) => (key in old || key in next) && old[key] !== next[key]);
    const ids = new Set([...(item.enrollment_ids || []), ...(item.enrollment_id ? [item.enrollment_id] : [])]);
    const affected = (exam.participants || []).filter((student) => ids.has(student.enrollment_id) && (!item.section_id || student.section_id === item.section_id));
    return `${affected.length ? `<p class="field-hint">Affected students: ${affected.map((student) => `${e(student.name)} (Section ${e(student.section_number)})`).join(", ")}</p>` : ""}${fields.length ? `<dl class="exams-change-values">${fields.map((key) => `<div><dt>${e(labels[key])}</dt><dd><span>${e(value(key, old[key]))}</span> → <strong>${e(value(key, next[key]))}</strong></dd></div>`).join("")}</dl>` : ""}${item.card_ids?.length ? `<p class="field-hint">${item.card_ids.length} retained student notice${item.card_ids.length === 1 ? "" : "s"} to check.</p>` : ""}`;
  }
  function bindFilters(container) {
    const search = container.querySelector("[data-student-search]"), select = container.querySelector("[data-status-filter]");
    if (!search) return;
    const filter = () => {
      let count = 0;
      container.querySelectorAll("[data-student-row]").forEach((row) => {
        const status = select?.value || "";
        const shown = !status || (status === "attention" ? !SETTLED.has(row.dataset.status) : row.dataset.status === status);
        row.hidden = !row.dataset.search.includes(search.value.trim().toLocaleLowerCase()) || !shown;
        if (!row.hidden) ++count;
      });
      container.querySelector("[data-filter-count]").textContent = `${count} shown`;
    };
    search.oninput = filter; if (select) select.onchange = filter; filter();
  }

  /** Set up grading now (requirement, 2026-10-02): the plan is made before any paper (POST /api/grading/assignments for the exam record, or
   * for the assessment, which makes its exam record), then its Rubrics & Templates step opens. */
  async function setUpGrading() {
    if (!hub) return;
    const title = hub.title, target = currentRoute();
    const plan = await api("/api/grading/assignments", "POST", hub.event_id ? {event_id: hub.event_id} : {assignment_id: hub.assignment_id, term_id: hub.term_id}, {}, 180000);
    if (currentRoute() !== target) return;
    location.hash = `#grading/${enc(plan.id)}?tab=rubrics`;
    toast(...setUpGradingNotice(title, plan));
  }

  async function openCreate(prefill = {}) {
    const intent = captureDialogIntent(document.querySelector("#dialog"));
    try {
      const found = await api(`${base}/catalog`);
      if (!intent()) return;
      catalog = found;
      const terms = catalog.terms || [];
      if (!terms.length) { toast("Create a course term and assignment before creating an exam.", true); return; }
      // An exam record is made for an assignment that already exists; a new paper starts in Create assignment.
      const createNew = {label: "Create assignment", run: () => { location.hash = "#documents?create=1"; }};
      if (!(catalog.assignments || []).length) { toast("Create the assignment first.", {tone: "warning", action: createNew}); return; }
      const form = formDialog("Create an exam", "For an assignment that already exists.", `<p class="field-hint exams-create-new">A new paper starts in <a href="#documents?create=1" data-exam-create-new>Create assignment</a>.</p><label class="field">Course and term<select aria-label="Course and term" name="term_id" required>${terms.map((item) => option(item.id, `${item.code || ""} · ${termLabel(item)}`)).join("")}</select></label><label class="field">Course assignment<select aria-label="Course assignment" name="assignment_id" required autofocus></select></label><label class="field">Exam title<input name="title" required maxlength="200" placeholder="Exam 1 · Fall 2026"></label><fieldset class="exams-checkboxes"><legend>Sections taking this exam</legend><div data-create-sections></div></fieldset>`, "Create exam", async (values, form, current) => {
        const sectionIds = values.getAll("section_id");
        if (!sectionIds.length) throw new Error("Choose at least one section.");
        const item = await api(base, "POST", {term_id: values.get("term_id"), assignment_id: values.get("assignment_id"), title: values.get("title"), section_ids: sectionIds});
        if (!current()) return; closeDialog(); location.hash = `#exams/${enc(item.id)}`; toast("Exam created.");
      });
      const update = () => {
        const term = terms.find((item) => item.id === form.elements.term_id.value);
        const assignments = (catalog.assignments || []).filter((item) => item.definition_id === term?.definition_id);
        form.elements.assignment_id.innerHTML = assignments.map((item) => option(item.id, item.title)).join("");
        const sections = (catalog.sections || []).filter((item) => item.term_id === term?.id);
        form.querySelector("[data-create-sections]").innerHTML = sections.map((item) => `<label><input type="checkbox" name="section_id" value="${e(item.id)}" checked>Section ${e(sectionLabel(item))}</label>`).join("") || '<p>No sections in this term.</p>';
        form.elements.title.value = assignments[0]?.title || "";
      };
      form.querySelector("[data-exam-create-new]").onclick = () => closeDialog();
      form.elements.term_id.onchange = update;
      form.elements.assignment_id.onchange = () => { form.elements.title.value = (catalog.assignments || []).find((item) => item.id === form.elements.assignment_id.value)?.title || ""; };
      // From an assessment's hub: its term and assignment chosen, everything still the instructor's to confirm.
      if (terms.some((item) => item.id === prefill.termId)) form.elements.term_id.value = prefill.termId;
      update();
      if (prefill.assignmentId && [...form.elements.assignment_id.options].some((item) => item.value === prefill.assignmentId)) {
        form.elements.assignment_id.value = prefill.assignmentId;
        form.elements.assignment_id.onchange();
      }
    } catch (cause) { alertMessage(cause); }
  }
  function openSitting(id = "") {
    const item = (exam.sittings || []).find((entry) => entry.id === id) || {kind: "main", label: ""};
    formDialog(id ? "Edit sitting" : "Add sitting", exam.title, `<label class="field">Label<input name="label" required maxlength="160" value="${e(item.label)}" placeholder="Main exam"></label><div class="exams-two-columns"><label class="field">Kind<select aria-label="Kind" name="kind">${[["main", "Main"], ["makeup", "Makeup"], ["dr", "DR testing"]].map(([value, label]) => option(value, label, item.kind)).join("")}</select></label><label class="field">Date<input name="date" type="date" value="${e(item.date)}"></label></div><label class="field">Local time<input name="time" maxlength="80" value="${e(item.time)}" placeholder="9:00 AM–10:15 AM"></label><label class="field">Room, optional<select aria-label="Room, optional" name="room_id">${option("", "No room selected")}${(catalog.rooms || []).map((room) => option(room.id, room.name, item.room_id)).join("")}</select></label><label class="field">Location or instructions<input name="location" maxlength="300" value="${e(item.location)}"></label>`, "Save sitting", async (values, form, current) => {
      await api(path(id ? `/sittings/${enc(id)}` : "/sittings"), id ? "PATCH" : "POST", {version: id ? item.version : exam.version, label: values.get("label"), kind: values.get("kind"), date: values.get("date"), time: values.get("time"), room_id: values.get("room_id") || null, location: values.get("location")});
      if (!current()) return; closeDialog(); await reload(); toast("Sitting saved.");
    });
  }
  function sittingOptions(selected = "") { return option("", "No sitting assigned", selected) + (exam.sittings || []).map((item) => option(item.id, item.label, selected)).join(""); }
  function openParticipant(key) {
    const item = (exam.participants || []).find((entry) => membership(entry) === key);
    if (!item) return;
    formDialog("Student exam record", `${item.name} · Section ${item.section_number}`, `<label class="field">Sitting<select aria-label="Sitting" name="sitting_id">${sittingOptions(item.sitting_id)}</select></label><label class="field">Expectation<select aria-label="Expectation" name="expectation">${Object.entries(EXPECTATIONS).map(([value, label]) => option(value, label, item.expectation)).join("")}</select></label><label class="field">Reason<textarea name="reason" maxlength="2000" placeholder="Record the reason for a change">${e(item.reason)}</textarea></label><p class="field-hint">This exam record does not change seating assignments or DR bookings.</p>`, "Save record", async (values, form, current) => {
      await api(path(`/participants/${enc(item.section_id)}/${enc(item.enrollment_id)}`), "PATCH", {version: item.version, sitting_id: values.get("sitting_id") || null, expectation: values.get("expectation"), reason: values.get("reason")});
      if (!current()) return; closeDialog(); await reload(); toast("Student exam record saved.");
    });
  }
  function openLink(kind) {
    const items = kind === "chart" ? exam.available_charts || [] : exam.available_batches || [];
    formDialog(kind === "chart" ? "Link a seating chart" : "Link a print job", "Use existing resources from this course and term.", `<label class="field">${kind === "chart" ? "Seating chart" : "Print job"}<select aria-label="Resource" name="resource_id" required>${option("", items.length ? "Choose a resource" : "No resources available")}${items.map((item) => option(item.id || item[`${kind}_id`], kind === "chart" ? `${item.room_name || item.title || "Seating chart"}${item.sections?.length ? ` · Sections ${item.sections.map(sectionLabel).join(", ")}` : ""}` : batchLabel(item))).join("")}</select></label><label class="field">Sitting<select aria-label="Sitting" name="sitting_id">${sittingOptions()}</select></label>`, "Link resource", async (values, form, current) => {
      await api(path(kind === "chart" ? "/charts" : "/batches"), "POST", {version: exam.version, [`${kind}_id`]: values.get("resource_id"), sitting_id: values.get("sitting_id") || null});
      if (!current()) return; closeDialog(); await reload(); toast("Resource linked.");
    });
  }

  function renameExam() {
    formDialog("Edit exam title", `${exam.code} · ${termLabel(exam)}`, `<label class="field">Title<input name="title" required maxlength="200" value="${e(exam.title)}"></label>`, "Save title", async (values, form, current) => { await api(path(), "PATCH", {version: exam.version, title: values.get("title")}); if (!current()) return; closeDialog(); await reload(); });
  }
  /** Each chart found for this exam's assignment, term and sections, linked by its own explicit, version-checked request. */
  async function linkFoundCharts() {
    const endpoint = path("/charts"), owner = exam;
    let version = exam.version, linked = 0;
    try {
      for (const chart of owner.available_charts || []) {
        const result = await api(endpoint, "POST", {version, chart_id: chart.id, sitting_id: null});
        version = result.version; ++linked;
      }
    } finally {
      if (linked && owner === exam) { await reload(); toast(`${linked} seating chart${linked === 1 ? "" : "s"} linked.`); }
    }
  }

  function bindActions(container) {
    container.querySelectorAll("[data-exam-action]").forEach((button) => {
      button.onclick = () => {
        const key = button.dataset.examAction;
        if (key === "create") void openCreate();
        else if (key === "refresh") { if (dirtyLeave()) { rubricDirty = false; markDirty = false; void render(true); } }
        else if (key === "tab-retry") void renderTab();
        else if (key === "link-found-charts") void run(button, () => linkFoundCharts());
        else if (key === "set-up-grading") void run(button, () => setUpGrading());
        else if (key === "show-all") { const select = container.querySelector("[data-status-filter]"); if (select) { select.value = ""; select.dispatchEvent(new Event("change")); } }
        else if (key === "add-sitting") openSitting();
        else if (key === "edit-sitting") openSitting(button.dataset.id);
        else if (key === "edit-participant") openParticipant(button.dataset.key);
        else if (key === "link-chart") openLink("chart");
        else if (key === "link-batch") openLink("batch");
        else if (key === "sync-roster") void run(button, async () => { await api(path("/roster"), "POST", {version: exam.version}); await reload(); toast("New roster students added; existing exam records retained."); });
        else if (key === "checkpoint") formDialog("Record your review", "Seats, cards and print files keep their existing state.", `<p>${e(exam.impacts?.changes?.length || 0)} changes will be included in this checkpoint.</p>`, "Mark reviewed", async (_values, form, current) => { await api(path("/checkpoint"), "POST", {version: exam.version, fingerprint: exam.impacts.fingerprint}); if (!current()) return; closeDialog(); await reload(); toast("Review checkpoint saved."); });
        else if (key === "add-request") openRequest();
        else if (key === "edit-request") openRequestDecision(button.dataset.id);
        else if (key === "apply-request") void openRequestApply(button.dataset.id);
        else if (key === "decision-card") openDecisionCard(button.dataset.id);
        else if (key === "sync-requests") void run(button, async () => {
          // Two existing explicit requests: sync Response Cards, then import what arrived. A card sync that cannot reach its
          // service still leaves the responses already synced to import.
          // The card sync already imports what arrived (for every exam), so its count joins the second request's.
          const endpoint = path("/requests/sync"), owner = exam;
          let note = "", synced = 0;
          try { synced = (await api("/api/response-cards/sync", "POST", {}))?.exam_requests_added || 0; } catch (cause) { note = ` Response Cards could not sync: ${cause.message}`; }
          const result = await api(endpoint, "POST", {});
          if (exam !== owner) return;
          await renderTab();
          const added = synced + (result.added || 0);
          toast(`${added ? `${added} new request${added === 1 ? "" : "s"} imported${synced ? " (across your exams)" : ""}.` : "No new requests."}${note}`, note ? {tone: "warning"} : undefined);
        });
        else if (key === "request-cards") openPrepareCards("request");
        else if (key === "feedback-cards") openPrepareCards("feedback");
      };
    });
  }

  function renderRequests(panel) {
    const items = requests.requests || [];
    const waiting = requests.waiting || 0;
    panel.innerHTML = `<section class="exams-block"><div class="exams-toolbar"><div><h3>Student requests</h3><p class="section-lead">Record your decision on each request, then preview any change before applying it.</p></div><div class="exams-actions">${action("add-request", "Record request")}${action("request-cards", "Prepare request cards")}${action("sync-requests", waiting ? `Check for new requests (${waiting.toLocaleString()} waiting)` : "Check for new requests")}</div></div>${disclosureMarkup({label: "How request cards work", key: "exams:request-cards", body: "<p>Request cards are Response Cards: prepare them here, then publish and send them from their issue. Check for new requests syncs Response Cards and imports any new answers here; responses also sync on their own every minute.</p>"})}${(requests.issues || []).length ? `<div class="exams-actions">${requests.issues.map((item) => issueLink(item.id, issueTitle(item))).join("")}</div>` : ""}${studentFilter(Object.keys(REQUEST_STATES))}${table(["Student", "Request", "Status", "Decision", ""], items.map((item) => {
      const student = (exam.participants || []).find((entry) => membership(entry) === membership(item)) || item;
      return `<tr ${studentRowAttrs({...student, ...item}, item.status)}><td>${e(item.name || student.name)}<small>Section ${e(item.section_number ?? student.section_number)}</small></td><td><strong>${e(REQUEST_KINDS[item.kind] || pretty(item.kind))}</strong><p class="exams-prewrap">${e(item.description)}</p><small>${e(when(item.created_at))}</small></td><td>${badge(item.status)}</td><td class="exams-prewrap">${e(item.decision || "—")}</td><td><div class="exams-actions">${!item.applied_hash ? action("edit-request", "Review", `data-id="${e(item.id)}"`) : ""}${item.status === "approved" && ["seat", "schedule", "makeup"].includes(item.kind) ? action("apply-request", "Preview change", `data-id="${e(item.id)}"`) : ""}${["approved", "declined", "resolved"].includes(item.status) ? action("decision-card", "Prepare decision card", `data-id="${e(item.id)}"`) : ""}</div></td></tr>`;
    }), "No requests recorded for this exam.")}</section>`;
  }
  /** A request-card issue's name with the placeholders this exam fixes filled in, as the Response Cards page names its issues. */
  function issueTitle(item) {
    const values = {"exam.event_name": exam.title, "exam.name": exam.assignment_title || exam.title, "assignment.name": exam.assignment_title || exam.title, "course.code": exam.code};
    return String(item.title || "Request cards").replace(/\{\{\s*([a-z_.]+)\s*\}\}/g, (match, key) => values[key] || match);
  }
  function openRequest() {
    formDialog("Record a student request", exam.title, `<label class="field">Student<select aria-label="Student" name="student" required>${option("", "Choose a student")}${(exam.participants || []).map((item) => option(membership(item), `${item.name} · Section ${item.section_number}`)).join("")}</select></label><label class="field">Request type<select aria-label="Request type" name="kind">${Object.entries(REQUEST_KINDS).map(([value, label]) => option(value, label)).join("")}</select></label><label class="field">Student's request<textarea name="description" required maxlength="5000"></textarea></label>`, "Record request", async (values, form, current) => {
      const [sectionId, enrollmentId] = String(values.get("student")).split("/");
      await api(path("/requests"), "POST", {section_id: sectionId, enrollment_id: enrollmentId, kind: values.get("kind"), description: values.get("description")});
      if (!current()) return; closeDialog(); await renderTab(); toast("Request recorded.");
    });
  }
  function openRequestDecision(id) {
    const item = (requests.requests || []).find((entry) => entry.id === id);
    if (!item) return;
    formDialog("Review request", item.name || exam.title, `<p class="exams-prewrap">${e(item.description)}</p><label class="field">Status<select aria-label="Status" name="status">${Object.entries(REQUEST_STATES).map(([value, label]) => option(value, label, item.status)).join("")}</select></label><label class="field">Decision or review notes<textarea name="decision" maxlength="5000">${e(item.decision)}</textarea></label><p class="field-hint">Approval records a decision. Use Preview change separately to apply a sitting or seat change.</p>`, "Save decision", async (values, form, current) => {
      await api(path(`/requests/${enc(id)}`), "PATCH", {version: item.version, status: values.get("status"), decision: values.get("decision")});
      if (!current()) return; closeDialog(); await renderTab(); toast("Decision saved.");
    });
  }
  async function openRequestApply(id) {
    const item = (requests.requests || []).find((entry) => entry.id === id);
    if (!item) return;
    const isSeat = item.kind === "seat";
    const form = formDialog("Preview an approved change", "The preview must still match when you apply the change.", `<p class="exams-prewrap">${e(item.description)}</p><label class="field" ${isSeat ? "hidden" : ""}>Change sitting<select aria-label="Change sitting" name="sitting_id" ${isSeat ? "disabled" : "required"}>${option("", "Choose a replacement sitting")}${(exam.sittings || []).map((sitting) => option(sitting.id, sitting.label)).join("")}</select></label><label class="field" ${isSeat ? "" : "hidden"}>Seating chart<select aria-label="Seating chart" name="chart_id" ${isSeat ? "required" : "disabled"}>${option("", "Choose a linked chart")}${(exam.charts || []).map((chart) => option(chart.chart_id || chart.id, chart.room_name || chart.title || "Seating chart")).join("")}</select></label><label class="field" ${isSeat ? "" : "hidden"}>New seat<select aria-label="New seat" name="seat_id" disabled>${option("", "Choose a chart first")}</select></label><button type="button" class="button" data-preview-change>Preview change</button><div data-change-preview class="exams-change-preview" aria-live="polite"></div>`, "Apply reviewed change", async (values, form, current) => {
      if (!preview) throw new Error("Preview the change before applying it.");
      await api(path(`/requests/${enc(id)}/apply`), "POST", {version: item.version, preview_hash: preview.preview_hash, ...(values.get("sitting_id") ? {sitting_id: values.get("sitting_id")} : {}), ...(values.get("seat_id") ? {seat_id: values.get("seat_id")} : {})});
      if (!current()) return; closeDialog(); await reload(); toast("Approved change applied. Review any affected cards and print materials.");
    });
    let preview = null, chartLoad = 0;
    const submit = form.querySelector('[type="submit"]'); submit.disabled = true;
    const invalidate = () => { preview = null; submit.disabled = true; form.querySelector("[data-change-preview]").textContent = "Preview required."; };
    form.elements.sitting_id.onchange = invalidate; form.elements.seat_id.onchange = invalidate;
    form.elements.chart_id.onchange = async () => {
      invalidate(); const token = ++chartLoad, id = form.elements.chart_id.value;
      form.elements.seat_id.disabled = true; form.elements.seat_id.innerHTML = option("", id ? "Loading seats…" : "No seat change");
      if (!id) return;
      try {
        const chart = await api(`/api/seating/${enc(id)}`);
        if (token !== chartLoad || !form.isConnected) return;
        const seats = chart.seats || chart.room?.seats || [];
        form.elements.seat_id.innerHTML = option("", "Choose an empty usable seat") + seats.filter((seat) => !seat.broken && !seat.blocked && !seat.student && !(Array.isArray(seat.conditions) ? seat.conditions : []).some((condition) => ["broken", "blocked"].includes(condition))).map((seat) => option(seat.id, seat.label)).join("");
        form.elements.seat_id.disabled = false;
      } catch (cause) { alertMessage(cause); }
    };
    const previewButton = form.querySelector("[data-preview-change]");
    previewButton.onclick = () => run(previewButton, async () => {
      const query = new URLSearchParams();
      if (form.elements.sitting_id.value) query.set("sitting_id", form.elements.sitting_id.value);
      if (form.elements.seat_id.value) query.set("seat_id", form.elements.seat_id.value);
      const selected = query.toString();
      const result = await api(path(`/requests/${enc(item.id)}/preview?${selected}`));
      const now = new URLSearchParams();
      if (form.elements.sitting_id.value) now.set("sitting_id", form.elements.sitting_id.value);
      if (form.elements.seat_id.value) now.set("seat_id", form.elements.seat_id.value);
      if (!form.isConnected || selected !== now.toString()) return;
      preview = result;
      form.querySelector("[data-change-preview]").innerHTML = `<h4>Review this change</h4><ul>${result.changes.map((change) => `<li>${e(change.message)}</li>`).join("")}</ul>${result.warnings.map((warning) => `<p class="callout exams-notice" data-tone="warning">${e(warning)}</p>`).join("")}`;
      submit.disabled = !result.preview_hash;
    });
  }
  function openPrepareCards(kind) {
    if (rubricDirty || markDirty) { toast("Save or discard the current grading changes before preparing cards.", true); return; }
    const id = requestId(), feedback = kind === "feedback";
    const released = (grading?.participants || []).filter((item) => item.status === "released" && item.release_id);
    if (feedback && !released.length) { toast("Release a student's finalized result before preparing feedback cards.", true); return; }
    formDialog(feedback ? "Prepare feedback card" : "Prepare student request cards", exam.title, `<p>${feedback ? "Prepare a personal card from the selected student's released grading record." : "Prepare a personal card for each selected exam student to report an exam issue."} Publishing and email remain separate actions in Response Cards.</p>${feedback ? `<label class="field">Released result<select aria-label="Released result" name="release_id" required>${released.map((item) => option(item.release_id, `${anonymous ? `Section ${item.section_number} · ${item.total} / ${item.max_points} points` : item.name} · released ${when(item.released_at)}`, work?.participant?.release_id)).join("")}</select></label>` : `<label class="field">Audience<select aria-label="Audience" name="student">${option("", "All exam participants")}${(exam.participants || []).map((item) => option(membership(item), `${item.name} · Section ${item.section_number}`)).join("")}</select></label>`}`, "Prepare cards", async (values, form, current) => {
      const payload = {request_id: id};
      if (feedback) payload.release_id = values.get("release_id");
      if (values.get("student")) { const [sectionId, enrollmentId] = String(values.get("student")).split("/"); payload.section_ids = [sectionId]; payload.enrollment_ids = [enrollmentId]; }
      const result = await api(path(feedback ? "/grading/feedback-cards" : "/request-cards"), "POST", payload);
      if (!current()) return; form.querySelector(".dialog-body").innerHTML = `<p>Cards prepared. Open the issue to review, publish and send them.</p><div class="exams-actions">${issueLink(result.issue_id)}</div>`;
      form.querySelector(".dialog-footer").innerHTML = '<button type="button" class="button" data-action="close">Close</button>';
      await renderTab();
    });
  }
  function openDecisionCard(request) {
    const item = (requests.requests || []).find((entry) => entry.id === request);
    if (!item) return;
    const id = requestId();
    formDialog("Prepare a decision card", item.name || exam.title, `<p class="exams-prewrap">${e(item.decision)}</p><p>Prepare a personal card with the retained decision. Review and publish it, then send it from Response Cards.</p>`, "Prepare decision card", async (_values, form, current) => {
      const result = await api(path(`/requests/${enc(item.id)}/decision-card`), "POST", {request_id: id});
      if (!current()) return; form.querySelector(".dialog-body").innerHTML = `<p>Decision card prepared.</p>${issueLink(result.issue_id)}`;
      form.querySelector(".dialog-footer").innerHTML = '<button type="button" class="button" data-action="close">Close</button>';
      await renderTab();
    });
  }

  function renderGrading(panel) {
    selectedDocument = grading.assignment_version_id || selectedDocument || grading.assignment_versions?.[0]?.id || "";
    grading.participants = (grading.participants || []).filter((item) => !item.assignment_version_id || !selectedDocument || item.assignment_version_id === selectedDocument);
    const participants = grading.participants || [], questions = grading.questions;
    if (!participants.some((item) => membership(item) === selectedStudent)) selectedStudent = participants[0] ? membership(participants[0]) : "";
    if (!questions.some((item) => item.id === selectedQuestion)) selectedQuestion = questions[0]?.id || "";
    rubricDraft = {assignment_version_id: selectedDocument, questions: structuredClone(questions)};
    const content = `<section class="exams-block"><div class="exams-toolbar"><div><h3>Manual scores</h3><p class="section-lead">Score one question across the class; finalize each record before releasing its feedback.</p></div><div class="exams-actions"><a class="button" href="${path("/grading/export")}">Download released grades CSV</a>${action("feedback-cards", "Prepare feedback cards")}</div></div><label class="field">Grade document version<select aria-label="Grade document version" data-grading-version>${(grading.assignment_versions || []).map((item) => option(item.id, `${item.label} · ${item.page_count} pages`, selectedDocument)).join("")}</select></label><p class="field-hint">Main and makeup versions retain their own questions, page regions and rubrics.</p><section class="template-grading-entry"><h3>Start from the blank paper</h3><p class="supporting-copy">Upload a grading template, map each problem's answer pages, then explicitly apply its saved mapping to this document version.</p><button class="button" type="button" data-grading-template>Manage grading templates</button></section><details class="exams-rubric" ${questions.length ? "" : "open"}><summary>Rubric and question regions</summary><div id="exam-rubric-editor"></div></details></section>
      ${questions.length ? `<section class="exams-block"><div class="exams-grading-controls"><label class="field">Question<select data-grading-question>${questions.map((item) => option(item.id, `${item.label} · ${item.max_points} points`, selectedQuestion)).join("")}</select></label><label class="field">Student paper<select data-grading-student>${studentOptions(participants)}</select></label><label class="exams-check"><input type="checkbox" data-hide-names ${anonymous ? "checked" : ""}>Hide roster names</label></div><p class="field-hint">Names written on the scanned paper may still be visible.</p><div id="exam-grading-work"></div></section>` : '<p class="callout exams-notice" data-tone="warning">Save a rubric to begin question-by-question grading.</p>'}`;
    // An exam graded with assisted grading says so first; the manual scorer stays one click away (open once it is in use).
    const planned = exam.grading_plan_id;
    const stage = (hub?.stages || []).find((item) => item.key === "grading");
    panel.innerHTML = planned
      ? `${calloutMarkup({tone: "info", title: `${exam.title} is graded in Grading`, body: stage?.detail || "", action: {label: "Open Grading", href: stage?.href?.startsWith("#grading/") ? stage.href : `#grading/${planned}`}})}${disclosureMarkup({label: "Score by hand instead", className: "exams-manual", open: questions.length > 0, body: content})}`
      : content;
    panel.querySelector("[data-grading-template]").onclick = () => {
      if (!dirtyLeave()) return;
      rubricDirty = false; markDirty = false;
      const version = grading.assignment_versions.find(item => item.id === selectedDocument);
      void templates.open({assignmentId: exam.assignment_id, eventId: exam.id,
        assignmentVersionId: selectedDocument, assignmentVersionLabel: version?.label,
        pageCount: version?.page_count, gradingVersion: grading.version, templateId: grading.template?.id, onApplied: () => renderTab()});
    };
    renderRubricEditor();
    panel.querySelector("[data-grading-version]").onchange = (event) => changeGradingVersion(event.target);
    panel.querySelector("[data-hide-names]")?.addEventListener("change", (event) => {
      anonymous = event.target.checked;
      const select = panel.querySelector("[data-grading-student]");
      select.innerHTML = studentOptions(participants);
      const name = panel.querySelector("[data-work-student-name]");
      if (name) { const index = participants.findIndex((item) => membership(item) === selectedStudent); name.textContent = studentName(participants[index], index); }
    });
    panel.querySelector("[data-grading-question]")?.addEventListener("change", (event) => {
      if (markDirty && !window.confirm("Discard the unsaved score and feedback?")) { event.target.value = selectedQuestion; return; }
      selectedQuestion = event.target.value; markDirty = false; void loadWork();
    });
    panel.querySelector("[data-grading-student]")?.addEventListener("change", (event) => {
      if (markDirty && !window.confirm("Discard the unsaved score and feedback?")) { event.target.value = selectedStudent; return; }
      selectedStudent = event.target.value; markDirty = false; void loadWork();
    });
    if (questions.length) void loadWork();
  }
  function criterionEditor(item, index) {
    return `<div class="exams-criterion-editor" data-criterion><label class="field">Criterion ID<input data-criterion-id value="${e(item.id)}" required maxlength="60" aria-label="Criterion ${index + 1} ID"></label><label class="field">Criterion<input data-criterion-label value="${e(item.label)}" required maxlength="300" aria-label="Criterion ${index + 1} label"></label><label class="field">Points<input data-criterion-points type="number" min="0" step="any" value="${e(item.points ?? 0)}" required aria-label="Criterion ${index + 1} points"></label><button type="button" class="text-button" data-remove-criterion aria-label="Remove criterion ${index + 1}">Remove</button></div>`;
  }
  function changeGradingVersion(select) {
    if (!dirtyLeave()) { select.value = selectedDocument; return; }
    selectedDocument = select.value; selectedStudent = ""; selectedQuestion = "";
    rubricDirty = false; markDirty = false; void renderTab();
  }
  function questionEditor(question, index) {
    const region = question.region || question.regions?.[0]?.region || {x: 0, y: 0, width: 1, height: 1};
    return `<fieldset class="exams-question-editor" data-question="${index}"><legend>Question ${index + 1}</legend><div class="exams-toolbar"><span class="field-hint">Reusable rubric and printed-page region${question.regions?.length > 1 ? ` · ${question.regions.length} saved answer areas; these controls edit the first` : ""}</span><button class="text-button" type="button" data-remove-question="${index}">Remove question</button></div><div class="exams-two-columns"><label class="field">Question ID<input data-q="id" required maxlength="60" value="${e(question.id)}"></label><label class="field">Label<input data-q="label" required maxlength="200" value="${e(question.label)}"></label><label class="field">Maximum points<input data-q="max_points" type="number" min="0" step="any" required value="${e(question.max_points ?? 10)}"></label><label class="field">Printed page number<input data-q="page" type="number" min="1" step="1" required value="${e(question.page || 1)}"></label></div><details><summary>Question crop, optional</summary><p class="field-hint">Fractions of the page, from 0 to 1. Full page: left 0, top 0, width 1, height 1.</p><div class="exams-region-fields">${[["x", "Left"], ["y", "Top"], ["width", "Width"], ["height", "Height"]].map(([key, label]) => `<label class="field">${label}<input data-region="${key}" type="number" min="0" max="1" step="any" required value="${e(region[key])}"></label>`).join("")}</div></details><div data-criteria>${(question.criteria || []).map(criterionEditor).join("")}</div><button type="button" class="button" data-add-criterion="${index}">Add scoring criterion</button><p class="field-hint">With criteria, the score is the sum of selected points. Without criteria, enter a score directly.</p><label class="field">Reusable feedback, one comment per line<textarea data-q="feedback" rows="3" maxlength="12000">${e((question.feedback || []).join("\n"))}</textarea></label></fieldset>`;
  }
  function captureRubric() {
    const form = root().querySelector("#exam-rubric-form");
    if (!form) return;
    rubricDraft.assignment_version_id = form.elements.assignment_version_id.value;
    rubricDraft.questions = [...form.querySelectorAll("[data-question]")].map((node) => {
      const value = (key) => node.querySelector(`[data-q="${key}"]`).value;
      const region = Object.fromEntries([...node.querySelectorAll("[data-region]")].map((input) => [input.dataset.region, Number(input.value)]));
      const previous = rubricDraft.questions[Number(node.dataset.question)];
      const areas = structuredClone(previous.regions || [{page: Number(value("page")), region}]);
      areas[0] = {page: Number(value("page")), region};
      return {...previous, regions: areas, id: value("id"), label: value("label"), max_points: Number(value("max_points")), page: Number(value("page")), region, criteria: [...node.querySelectorAll("[data-criterion]")].map((row) => ({id: row.querySelector("[data-criterion-id]").value, label: row.querySelector("[data-criterion-label]").value, points: Number(row.querySelector("[data-criterion-points]").value)})), feedback: value("feedback").split("\n").map((line) => line.trim()).filter(Boolean)};
    });
  }
  function renderRubricEditor() {
    const target = root().querySelector("#exam-rubric-editor");
    if (!target) return;
    target.innerHTML = `<form id="exam-rubric-form"><p class="field-hint">Saving a changed rubric creates a retained rubric revision. Existing grades must be reviewed against the new rubric.</p><label class="field">Assignment document version<select aria-label="Assignment document version" name="assignment_version_id" required>${option("", "Choose the printed version")}${(grading.assignment_versions || []).map((item) => option(item.id, `${item.label} · ${item.page_count} pages`, rubricDraft.assignment_version_id)).join("")}</select></label><div>${rubricDraft.questions.map(questionEditor).join("")}</div><div class="exams-toolbar"><button type="button" class="button" data-add-question>Add question</button><span class="field-hint" data-rubric-status role="status">${rubricDirty ? "Unsaved changes" : ""}</span><button class="button primary" type="submit">Save rubric</button></div>${errorLine()}</form>`;
    const form = target.querySelector("form");
    const dirty = () => { rubricDirty = true; form.querySelector("[data-rubric-status]").textContent = "Unsaved changes"; };
    form.oninput = (event) => { if (event.target.name !== "assignment_version_id") dirty(); };
    form.elements.assignment_version_id.onchange = () => changeGradingVersion(form.elements.assignment_version_id);
    form.querySelector("[data-add-question]").onclick = () => {
      captureRubric(); let n = rubricDraft.questions.length + 1;
      while (rubricDraft.questions.some((item) => item.id === `q${n}`)) ++n;
      rubricDraft.questions.push({id: `q${n}`, label: `Question ${n}`, max_points: 10, page: 1, criteria: [], feedback: [], region: {x: 0, y: 0, width: 1, height: 1}}); rubricDirty = true; renderRubricEditor();
    };
    form.querySelectorAll("[data-remove-question]").forEach((button) => { button.onclick = () => { captureRubric(); rubricDraft.questions.splice(Number(button.dataset.removeQuestion), 1); rubricDirty = true; renderRubricEditor(); }; });
    form.querySelectorAll("[data-add-criterion]").forEach((button) => { button.onclick = () => {
      captureRubric(); const question = rubricDraft.questions[Number(button.dataset.addCriterion)]; let n = question.criteria.length + 1;
      while (question.criteria.some((item) => item.id === `c${n}`)) ++n;
      question.criteria.push({id: `c${n}`, label: "", points: 1}); rubricDirty = true; renderRubricEditor();
    }; });
    form.querySelectorAll("[data-remove-criterion]").forEach((button) => { button.onclick = () => { button.closest("[data-criterion]").remove(); dirty(); }; });
    form.onsubmit = (event) => {
      event.preventDefault(); captureRubric();
      void perform(form, async (current) => {
        if (markDirty) throw new Error("Save or discard the current score before changing the rubric.");
        await api(path("/grading/rubric"), "PUT", {version: grading.version, ...rubricDraft}); if (!current()) return; rubricDirty = false;
        await renderTab(); toast("Rubric saved.");
      });
    };
  }
  async function loadWork() {
    const target = root().querySelector("#exam-grading-work");
    if (!target) return;
    if (!selectedStudent || !selectedQuestion) { target.innerHTML = '<p class="exams-empty">No students available for grading.</p>'; return; }
    const token = ++workGeneration, eventId = exam.id;
    const summary = (grading.participants || []).find((item) => membership(item) === selectedStudent);
    if (summary?.source_error) { showWorkError(target, summary.source_error); return; }
    target.innerHTML = '<p class="loading-state">Loading reviewed paper…</p>';
    const [sectionId, enrollmentId] = selectedStudent.split("/");
    const query = new URLSearchParams({section_id: sectionId, enrollment_id: enrollmentId, question_id: selectedQuestion, anonymous: anonymous ? "1" : "0"});
    try {
      const result = await api(path(`/grading/work?${query}`));
      if (token !== workGeneration || exam?.id !== eventId || tab !== "grading" || !target.isConnected || root().hidden) return;
      work = result;
      const previous = (grading.participants || []).findIndex((item) => membership(item) === membership(result.participant));
      if (previous !== -1) {
        const name = grading.participants[previous].name;
        grading.participants[previous] = {...grading.participants[previous], ...result.participant, name};
        const select = root().querySelector("[data-grading-student]");
        select.innerHTML = studentOptions(grading.participants);
      }
      renderWork(target);
    } catch (cause) {
      if (token !== workGeneration || !target.isConnected) return;
      showWorkError(target, cause.message);
    }
  }
  function showWorkError(target, message) {
    const record = (exam.reconciliation?.students || []).find((item) => membership(item) === selectedStudent);
    const gradeRecord = (grading.participants || []).find((item) => membership(item) === selectedStudent);
    const sourceLinks = [...new Set(gradeRecord?.source_job_ids || [])].map((id) => `<a class="button" href="#inbox/job/${enc(id)}">Review source in Inbox</a>`).join("");
    target.innerHTML = `<div class="callout exams-notice" data-tone="warning" role="alert"><p>${e(message)}</p><p>Resolve the paper's reviewed submission and finish its scan checks before grading.</p>${sourceLinks || copyLinks(record?.copies)}<button type="button" class="button" data-work-retry>Refresh grading</button></div>`;
    target.querySelector("[data-work-retry]").onclick = () => { if (dirtyLeave()) { rubricDirty = false; markDirty = false; void renderTab(); } };
  }
  function renderWork(target) {
    const question = work.question, mark = work.mark || {}, participant = work.participant || {};
    const students = grading.participants || [], index = students.findIndex((item) => membership(item) === selectedStudent);
    const selected = students[index] || participant;
    const status = participant.status || selected.status || "draft", locked = !!participant.finalized_at || ["finalized", "released"].includes(status);
    const areas = work.question_regions;
    const pageMarkup = areas.map((area, index) => {
      const url = !area.missing && /^\/api\/inbox\/artifacts\/[^/?#]+\/download$/.test(area.raster_url || "") ? area.raster_url : "";
      return `<section class="exams-question-area">${areas.length > 1 ? `<strong>Printed page ${e(area.page)} · area ${index + 1}</strong>` : ""}${url ? `<div class="exams-scan-frame" data-work-area="${index}"><img class="exams-scan-image" src="${e(url)}" alt="Reviewed scan of printed page ${e(area.page)}, answer area ${index + 1}" loading="eager"></div>` : `<div class="exams-empty">Page ${e(area.page)} is missing from the reviewed submission.</div>`}</section>`;
    }).join("");
    target.innerHTML = `<div class="exams-toolbar exams-work-heading"><div><h4 data-work-student-name>${e(studentName(selected, index))}</h4><p>${e(question.label)} · ${badge(status)}${participant.total != null ? ` · ${e(participant.total)} / ${e(participant.max_points)} total points` : ""}</p></div><div class="exams-actions"><button type="button" class="button" data-work-prev ${index <= 0 ? "disabled" : ""}>← Previous paper</button><button type="button" class="button" data-work-next ${index >= students.length - 1 ? "disabled" : ""}>Next paper →</button></div></div><div class="exams-grading-layout"><div class="exams-paper"><div class="exams-toolbar"><strong>${areas.length > 1 ? `${areas.length} answer areas` : `Printed page ${e(question.page)}`}</strong><label class="exams-check"><input type="checkbox" data-full-page>Show full page</label>${work.assembly_revision_id ? `<a ${viewLink(`/api/documents/assemblies/${enc(work.assembly_revision_id)}/download`, "Reviewed paper", e)}>Open reviewed PDF</a>` : ""}</div>${pageMarkup}</div><form id="exam-mark-form" class="exams-mark-form"><h4>Score and feedback</h4><div class="exams-scoring-criteria">${(question.criteria || []).map((criterion) => `<label class="exams-check"><input type="checkbox" name="criterion_ids" value="${e(criterion.id)}" data-points="${e(criterion.points)}" ${(mark.criterion_ids || []).includes(criterion.id) ? "checked" : ""} ${locked ? "disabled" : ""}><span>${e(criterion.label)} <strong>${e(criterion.points)} points</strong></span></label>`).join("")}</div><label class="field">Score out of ${e(question.max_points)}<input name="score" type="number" min="0" max="${e(question.max_points)}" step="any" required value="${e(mark.score ?? "")}" ${question.criteria?.length ? "readonly" : ""} ${locked ? "disabled" : ""}></label><label class="field">Feedback<textarea name="feedback" rows="6" maxlength="10000" ${locked ? "disabled" : ""}>${e(mark.feedback)}</textarea></label>${question.feedback?.length ? `<div class="exams-feedback-chips">${question.feedback.map((text, i) => `<button type="button" class="button" data-feedback-index="${i}" ${locked ? "disabled" : ""}>${e(text)}</button>`).join("")}</div>` : ""}${work.page_missing ? `<label class="exams-check exams-notice"><input type="checkbox" name="acknowledge_missing" ${locked ? "disabled" : ""}>I reviewed the missing page and approve entering this score.</label>` : ""}<span data-mark-status class="field-hint" role="status"></span>${errorLine()}<div class="exams-actions"><button class="button primary" type="submit" ${locked ? "disabled" : ""}>Save score</button><button class="button" type="submit" name="next" value="1" ${locked || index >= students.length - 1 ? "disabled" : ""}>Save & next paper</button></div><div class="exams-record-actions">${locked ? '<button class="button" type="button" data-grade-control="reopen">Reopen grading</button>' : '<button class="button" type="button" data-grade-control="finalize">Finalize this student</button>'}${status === "finalized" ? '<button class="button primary" type="button" data-grade-control="release">Release this result</button>' : ""}</div><details class="exams-history"><summary>Score history</summary>${(work.history || []).map((item) => `<div><strong>${e(item.score ?? item.mark?.score ?? "—")} points</strong> · ${e(when(item.created_at))}<p class="exams-prewrap">${e(item.feedback || item.mark?.feedback || item.reason || "")}</p></div>`).join("") || '<p>No previous scores.</p>'}</details></form></div>`;
    const form = target.querySelector("#exam-mark-form");
    const dirty = () => { markDirty = true; form.querySelector("[data-mark-status]").textContent = "Unsaved score or feedback"; };
    form.oninput = dirty;
    const sumCriteria = () => { if (question.criteria?.length) form.elements.score.value = String([...form.querySelectorAll('[name="criterion_ids"]:checked')].reduce((total, item) => total + Number(item.dataset.points), 0)); };
    form.querySelectorAll('[name="criterion_ids"]').forEach((input) => { input.onchange = () => { sumCriteria(); dirty(); }; }); sumCriteria();
    form.querySelectorAll("[data-feedback-index]").forEach((button) => { button.onclick = () => { const comment = question.feedback[Number(button.dataset.feedbackIndex)]; form.elements.feedback.value = [form.elements.feedback.value.trim(), comment].filter(Boolean).join("\n"); dirty(); }; });
    const navigate = (step) => {
      if (markDirty && !window.confirm("Discard the unsaved score and feedback?")) return;
      selectedStudent = membership(students[index + step]); markDirty = false;
      root().querySelector("[data-grading-student]").value = selectedStudent; void loadWork();
    };
    target.querySelector("[data-work-prev]").onclick = () => navigate(-1); target.querySelector("[data-work-next]").onclick = () => navigate(1);
    form.onsubmit = (event) => {
      event.preventDefault(); const values = new FormData(form), next = event.submitter?.name === "next";
      void perform(form, async (current) => {
        const [sectionId, enrollmentId] = selectedStudent.split("/");
        await api(path("/grading/marks"), "POST", {section_id: sectionId, enrollment_id: enrollmentId, question_id: selectedQuestion, assembly_revision_id: work.assembly_revision_id, version: work.version ?? participant.version, score: Number(values.get("score")), feedback: values.get("feedback"), criterion_ids: values.getAll("criterion_ids"), acknowledge_missing: values.has("acknowledge_missing")});
        if (!current()) return;
        markDirty = false;
        if (next && students[index + 1]) { selectedStudent = membership(students[index + 1]); root().querySelector("[data-grading-student]").value = selectedStudent; }
        await loadWork(); toast("Score saved.");
      });
    };
    form.querySelectorAll("[data-grade-control]").forEach((button) => { button.onclick = () => openGradeControl(button.dataset.gradeControl); });
    const cropFunctions = [];
    target.querySelectorAll("[data-work-area]").forEach(frame => {
      const image = frame.querySelector("img"), area = areas[Number(frame.dataset.workArea)];
      const applyCrop = () => {
        // An image can finish loading after the work view was replaced (another paper, a refresh): nothing left to crop.
        const toggle = target.querySelector("[data-full-page]");
        if (!toggle || !image.isConnected) return;
        const full = toggle.checked;
        let region = full ? {x: 0, y: 0, width: 1, height: 1} : area.region || {x: 0, y: 0, width: 1, height: 1};
        if (![region.x, region.y, region.width, region.height].every(Number.isFinite) || region.width <= 0 || region.height <= 0) region = {x: 0, y: 0, width: 1, height: 1};
        // CSSOM assignment is permitted by the page CSP; no inline style markup.
        frame.style.aspectRatio = String((image.naturalWidth || 850) * region.width / ((image.naturalHeight || 1100) * region.height));
        image.style.width = `${100 / region.width}%`; image.style.left = `${-100 * region.x / region.width}%`; image.style.top = `${-100 * region.y / region.height}%`;
      };
      cropFunctions.push(applyCrop);
      image.onload = applyCrop;
      image.onerror = () => { frame.textContent = "The scan image could not be loaded. Open the reviewed PDF or refresh this paper."; };
      if (image.complete) applyCrop();
    });
    target.querySelector("[data-full-page]").onchange = () => cropFunctions.forEach(apply => apply());
  }

  function openGradeControl(kind) {
    if (markDirty) { toast("Save the current score and feedback first.", true); return; }
    if (rubricDirty) { toast("Save or discard rubric changes first.", true); return; }
    const names = {finalize: "Finalize this student's grades", reopen: "Reopen grading", release: "Release this result"};
    const id = requestId(), basis = work, student = selectedStudent, participant = basis.participant || {};
    const explanation = kind === "finalize" ? "All questions must have scores for this reviewed submission. Finalizing prevents edits until you explicitly reopen grading." : kind === "release" ? "Save an immutable released result for feedback cards and CSV export. This does not publish a card or send email." : "Retain the earlier scores and record why grading is being reopened.";
    formDialog(names[kind], exam.title, `<p>${e(explanation)}</p>${kind === "reopen" ? '<label class="field">Reason<textarea name="reason" required maxlength="2000"></textarea></label>' : ""}`, names[kind], async (values, form, current) => {
      const [sectionId, enrollmentId] = student.split("/");
      await api(path(`/grading/${kind}`), "POST", {section_id: sectionId, enrollment_id: enrollmentId, version: basis.version ?? participant.version, assembly_revision_id: basis.assembly_revision_id, request_id: id, ...(kind === "reopen" ? {reason: values.get("reason")} : {})});
      if (!current()) return; closeDialog(); await loadWork(); toast(kind === "release" ? "Released result retained. Prepare feedback cards when ready." : kind === "finalize" ? "Student grading finalized." : "Grading reopened.");
    });
  }

  // A list card opens its exam from anywhere on the card; its own links and buttons keep their own targets.
  document.addEventListener("click", (event) => {
    const card = event.target instanceof Element ? event.target.closest("#exams-section .exams-card[data-href]") : null;
    if (!card || event.target.closest("a, button, summary, input, select, textarea, label") || event.defaultPrevented) return;
    location.hash = card.dataset.href;
  });
  // Unsaved rubric or score edits: the shell asks before any hash change or unload (ui.js registerLeaveGuard), with the same
  // question the page's own tab and paper switches ask.
  registerLeaveGuard(() => (rubricDirty || markDirty) && root() && !root().hidden ? LEAVE_QUESTION : null);
  return {render, openCreate};
}
