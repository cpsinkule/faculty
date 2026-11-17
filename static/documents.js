import { createPrintDriveExporter } from "./print-drive.js";
import { createAssignmentWizard, setUpGradingNotice } from "./assignment-wizard.js";
import { createQuizDrafts } from "./quiz-drafts.js";
import { createCanvasPlacement } from "./canvas-placement.js";
import { jobFiles, exportFiles, stackFiles, isStampsOnly, bindPrintFiles, downloadable } from "./print-files.js";
import { actionMenuMarkup, calloutMarkup, captureDialogIntent, dialogSession, emptyMarkup, rememberedOpen, wallDate, whenVisible, setBusy } from "./ui.js";
import { studentPicker } from "./student-picker.js";
import { pipelineMarkup } from "./grading-guide.js";
import {icon} from "./icons.js";
import { viewLink } from "./file-viewer.js";
import {patchChildren} from './dom-patch.js';
import { attachPrefill, followRecord, openRows } from "./schedule-prefill.js";

// Human names for a print job's audit events (raw kinds stay in the catalog and the manifest).
const EVENT_NAMES = {
  batch_allocated: "Print job created", rendering: "Preparing pages", issued: "Pages checked and issued", build_failed: "Preparing failed",
  interrupted: "Preparing interrupted", cancel: "Cancelled", supersede: "Superseded", retry: "Retried", labels_issued: "Labels prepared",
  copy_assignment: "Copy's student changed", seat_marks: "Seat numbers beside stamps changed", label_mode: "Label mode changed",
  sequence_identity_issued: "Stamps issued by position", email_export_approved: "Email export approved", email_export_accepted: "Email export accepted",
  email_export_failed: "Email export not sent", email_export_uncertain: "Email export unconfirmed", email_export_sending: "Email export sending",
  printer_send_approved: "Sent to the printer", printer_send_accepted: "Printer accepted the job", printer_send_failed: "Printer send not sent",
  printer_send_uncertain: "Printer send unconfirmed",
  assembly_selected: "Reviewed pages saved",
};
const eventName = kind => EVENT_NAMES[kind] || String(kind || "").replace(/[_-]+/g, " ").replace(/^./, c => c.toUpperCase());

export function createDocuments({ api, escape, toast, showDialog, dialogHeader, header, getSeating, refresh }) {
  bindPrintFiles();
  let workspaceSurface = null;
  const activeWorkspace = () => workspaceSurface?.host.isConnected && workspaceSurface.active();
  const refreshSurface = () => {
    if (activeWorkspace()) return workspaceSurface.onChanged();
    if (!document.querySelector('#documents-section').hidden) return render(true);
    // ASSIGNMENT-WORKSPACE: the Assignments list, where Create assignment opened the window, lists what it created.
    document.dispatchEvent(new CustomEvent('faculty:assignments-changed'));
  };
  const exportToDrive = createPrintDriveExporter({escape, showDialog, dialogHeader});
  const wizard = createAssignmentWizard({api, escape, toast, showDialog, dialogHeader, getSeating, refresh,
    onChanged: () => { void Promise.resolve(refreshSurface()).catch(e => toast(e.message, true)); },
    openBatch: id => detail(id).catch(e => toast(e.message, true))});
  // Quizzes drafted by a model (quiz-drafts.js): drafts listed above the assignment cards, each on its own page, #documents/quiz/<id>.
  const quizDrafts = createQuizDrafts({api, escape, toast, showDialog, dialogHeader, header,
    onChanged: () => { if (!document.querySelector("#documents-section").hidden) render(true).catch(e => toast(e.message, true)); }});
  // "Put on Canvas" (canvas-placement.js): a card's assignment into the term's Canvas copy; where it stands per term, from
  // Faculty's records (GET .../assignments/status), read after the catalog and kept half a minute.
  const canvasPlacement = createCanvasPlacement({api, escape, toast, showDialog, dialogHeader});
  const canvasStatus = new Map();
  // Makeups owed per exam record (ABSENCES "MAKEUPS", /api/absences/makeups), read at most every 30 s: {at, counts: Map(event → n)}.
  let makeupsOwed = {at: 0, counts: new Map()};
  let drafts = [];
  let catalog;
  let refreshTimer;
  let generation = 0;
  let route = "";
  let pendingDeepLink = null;
  let workspaceLink = null;  // an old paper/print link (?assignment, ?jobs, ?batch, ?chart) whose window or job is still to open
  // Such a link keeps its address once opened, so leaving the page (its section hidden) forgets the address: following the same
  // link again (Back, or the same link from another page) opens its window again.
  new MutationObserver(() => { if (document.querySelector("#documents-section").hidden) route = ""; })
    .observe(document.querySelector("#documents-section"), {attributes: true, attributeFilter: ["hidden"]});
  let missingQuiz = null;  // the quiz draft a link named that the server does not have (#documents/quiz/<id>)
  const library = {tab: "assignments", query: "", course: "", status: "current", assignment: "", page: 1};
  const PAGE_SIZE = 8;
  const renderedResults = new WeakMap();
  const working = new Set(["pending", "rendering", "validating"]);
  const statusNames = {pending: "Queued", rendering: "Preparing", validating: "Checking", ready: "Ready to print", failed: "Needs attention", cancelled: "Cancelled", superseded: "Superseded"};
  const statusTones = {pending: "info", rendering: "info", validating: "info", ready: "success", failed: "danger", cancelled: "neutral", superseded: "neutral"};
  const plural = (count, word, many = `${word}s`) => `${Number(count).toLocaleString()} ${count === 1 ? word : many}`;
  const shortDate = iso => { const day = new Date(iso); return day.toLocaleDateString(undefined, {month: "short", day: "numeric", ...(day.getFullYear() === new Date().getFullYear() ? {} : {year: "numeric"})}); };
  const termText = s => s ? `${s.term === "Other" ? s.term_other : s.term} ${s.year}` : "";
  const versionText = v => !v ? "Retained PDF version" : isStampsOnly(v) ? v.label : `Version ${v.label}`;
  // Names of a section's roster for the copy list and pickers: read under the roster lock on the server, kept a minute here.
  const rosterCache = new Map();
  const dialog = () => document.querySelector("#dialog");
  const opening = () => captureDialogIntent(dialog());
  function sectionStudents(sectionId) {
    const cached = rosterCache.get(sectionId);
    if (cached && Date.now() - cached.at < 60000) return cached.promise;
    const promise = api(`/api/documents/sections/${encodeURIComponent(sectionId)}/enrollments`);
    rosterCache.set(sectionId, {at: Date.now(), promise});
    promise.catch(() => rosterCache.delete(sectionId));
    return promise;
  }
  // A job printed for the whole term names no section (requirement, 2026-10-01: one stack, any copy for any student of the
  // sections it lists). Its copies' names come from every section's roster of the term, each student in their own section.
  function termStudents(termId) {
    const key = `term:${termId}`, cached = rosterCache.get(key);
    if (cached && Date.now() - cached.at < 60000) return cached.promise;
    const promise = api(`/api/documents/terms/${encodeURIComponent(termId)}/enrollments`);
    rosterCache.set(key, {at: Date.now(), promise});
    promise.catch(() => rosterCache.delete(key));
    return promise;
  }
  const stackPlace = list => `${list?.length ? `Section${list.length === 1 ? "" : "s"} ${list.map(s => s.number).join(", ")}` : "All sections"} · one stack`;
  const placeOf = b => b.section_id ? `Section ${b.section_number ?? b.section?.number ?? ""}` : stackPlace(b.sections);
  const pictogram = kind => icon(kind === 'arrow' ? 'arrow-right' : kind);
  // What the page counts, ONE rule (print-6): the Print jobs list opens on Current, and the tab badges, the results line and an
  // assignment card's "N print jobs" count what that default list shows. Archived jobs (certified grades, a view) and superseded or
  // cancelled ones (history: Status → Cancelled & superseded, or All statuses) are left out; jobs first-page registration issued
  // stay folded apart under "Issued for scanned papers" with their own count, never counted as print jobs. A card's count is the
  // list its button opens (`jobsOf`): its current jobs, or, when it has none and some are archived, the Archived status's list.
  const archivedJob = b => Boolean(b.archived);
  const endedJob = b => b.state === "superseded" || b.state === "cancelled";
  const currentJob = b => !archivedJob(b) && !endedJob(b);
  const countedJob = b => currentJob(b) && b.origin !== "registration";
  // The Status filter, one rule for the list, a card's count and the empty list's advice. Archived jobs (an assessment whose
  // grades are certified: a view, certified_archive.py) leave every status but their own, failed ones included: certified grades
  // resolve them (requirement, 2026-09-27); the jobs are unchanged. Archived lists them all, superseded and cancelled ones too.
  function statusMatches(b, status) {
    if (status === "archived") return archivedJob(b);
    if (archivedJob(b)) return false;
    return status === "all" || (status === "current" ? !endedJob(b) : status === "working" ? working.has(b.state)
      : status === "attention" ? b.state === "failed" : status === "history" ? endedJob(b) : b.state === status);
  }
  // One assignment's print jobs as its card's "N print jobs" opens them (`showJobs`): the current ones, else (none current, some
  // archived) every archived one, as the Archived status lists them. Registration jobs never count.
  function jobsOf(assignmentId, jobs) {
    const own = jobs.filter(b => b.assignment?.id === assignmentId && b.origin !== "registration");
    const current = own.filter(b => statusMatches(b, "current"));
    return current.length || !own.some(archivedJob) ? {status: "current", jobs: current} : {status: "archived", jobs: own.filter(archivedJob)};
  }
  // The catalog's jobs with what the page shows of them: state (superseded wins), version, section, term and assignment.
  function libraryJobs() {
    const assignments = new Map(catalog.assignments.map(a => [a.id, a]));
    const versions = new Map(catalog.versions.map(v => [v.id, v]));
    const sections = new Map(catalog.sections.map(s => [s.id, s]));
    const termRows = new Map(catalog.sections.map(s => [s.term_id, s]));  // any section of a term carries its course and term
    return catalog.batches.map(b => { const version = versions.get(b.assignment_version_id); return {...b, state: b.superseded_at ? "superseded" : b.status, version, stamps_only: isStampsOnly(version), section: sections.get(b.section_id), termRow: b.section_id ? sections.get(b.section_id) : termRows.get(b.term_id), assignment: assignments.get(version?.assignment_id)}; });
  }
  const sectionLabel = (s) => `${s.code} · ${s.term === "Other" ? s.term_other : s.term} ${s.year} · Section ${s.number}`;

  async function perform(form, operation) {
    const button = form.querySelector('button[type="submit"]');
    if (button?.disabled) return;
    setBusy(button, true);
    const error = form.querySelector('[role="alert"]');
    if (error) error.textContent = "";
    try { await operation(); }
    catch (cause) { if (error) error.textContent = cause.message; else toast(cause.message, true); }
    finally { setBusy(button, false); }
  }

  // The page header names the page and holds its one creation entry (task 12); the view sets it on every render.
  function setHeader() {
    header?.set({route: location.hash, title: "Assignments", subtitle: "Create assignments and quizzes, and print their stamped copies.",
      action: {label: "Create assignment", icon: "plus", run: () => wizard.open({definitionId: library.course})},
      secondary: [{label: "Draft a quiz with a model", icon: "spark", run: () => quizDrafts.openCreate(library.course).catch(e => toast(e.message, true))}]});
  }

  async function render(force = false) {
    clearTimeout(refreshTimer);
    const root = document.querySelector("#documents-section");
    const changedRoute = route !== location.hash;
    route = location.hash;
    const params = new URLSearchParams(location.hash.split("?")[1]);
    // Old paper/print links open the same scoped working area as Create. Keep their
    // address while consuming only the modal action, so refresh cannot lose the term.
    if (['assignment', 'jobs', 'batch', 'chart'].some(key => params.has(key))) {
      const target = route;
      // The link's window or job opens once, after the first read that succeeds: a failed read keeps it for Try again,
      // and later refreshes of the same address never open it again.
      if (changedRoute) workspaceLink = target;
      let resolved;
      try {
        resolved = await api(`/api/assignments/resolve?route=${encodeURIComponent(target)}`);
        if (location.hash !== target) return;
        if (resolved.context) await renderWorkspace(root, resolved.context, () => location.hash === target);
      } catch (error) {
        if (location.hash !== target) return;
        workspaceFailed(root, error);
        return;
      }
      if (location.hash !== target) return;
      if (resolved.context) {
        header?.set({route: target, title: resolved.context.title, subtitle: 'Paper and printing', action: null, secondary: []});
        if (workspaceLink === target) {
          workspaceLink = null;
          if (params.get('assignment')) {
            const assignment = catalog.assignments.find(a => a.id === resolved.context.assignment_id);
            await wizard.openForAssignment(assignment, params.get('step') || 'details', resolved.context.term_id);
          } else if (params.get('batch')) await detail(params.get('batch'));
          else if (params.get('chart')) await wizard.open({chartId: params.get('chart')});
        }
        return;
      }
      workspaceLink = null;
    }
    if (changedRoute) pendingDeepLink = params.get("chart") || params.get("batch") || params.get("assignment") || params.get("jobs") || params.get("create") === "1" || params.get("quiz") === "new"
      ? {route, chartId: params.get("chart"), batchId: params.get("batch"), assignmentId: params.get("assignment"), step: params.get("step") || "details",
        jobs: params.get("jobs"), create: params.get("create") === "1", quiz: params.get("quiz") === "new"} : null;
    if (changedRoute && params.get("definition")) Object.assign(library, {course: params.get("definition"), assignment: "", page: 1});
    // ASSIGNMENT-WORKSPACE: a bare #documents opens Assignments, so every print job keeps its own address here:
    // #documents?tab=batches, optionally with &status=attention (Home's print failures, Workspace settings).
    const jobsLink = changedRoute && params.get("tab") === "batches";
    if (jobsLink) Object.assign(library, {tab: "batches", assignment: "", page: 1,
      status: ["current", "all", "ready", "working", "attention", "history", "archived"].includes(params.get("status")) ? params.get("status") : "current"});
    if (!root.querySelector("#document-library-root")) root.innerHTML = '<div id="document-library-root"></div><div id="quiz-draft-root" hidden></div>';
    const libraryRoot = root.querySelector("#document-library-root"), quizRoot = root.querySelector("#quiz-draft-root");
    const quiz = location.hash.split("?")[0].match(/^#documents\/quiz\/([^/]+)$/);
    if (quiz) {
      libraryRoot.hidden = true;
      quizRoot.hidden = false;
      const id = decodeURIComponent(quiz[1]);
      // A draft found missing is not read again until the address changes: the shell's next render must not ask once more.
      if (id === missingQuiz && !changedRoute && !force) return;
      missingQuiz = null;
      try { await quizDrafts.renderPage(quizRoot, id, force || changedRoute); }
      catch (error) {
        // A draft drawn already keeps its page and says the read failed (and reads again); a link to a missing draft, or a first
        // read that failed, gets a way back instead of a blank page.
        if (error?.status !== 404 && quizDrafts.readFailed(quizRoot, id, error)) return;
        quizDrafts.stop();
        if (error?.status === 404) missingQuiz = id;
        header?.set({route: location.hash, title: "Quiz draft", subtitle: "", crumbs: [{label: "Home", href: "#overview"}, {label: "Assignments", href: "#assignments"}, {label: "Quiz draft"}], action: null, secondary: []});
        quizRoot.innerHTML = emptyMarkup({title: "This quiz draft is not available", body: error.message, action: {label: "Assignments", href: "#assignments"}});
      }
      return;
    }
    quizDrafts.stop();
    quizRoot.hidden = true;
    libraryRoot.hidden = false;
    setHeader();
    if (!root.querySelector("#document-assignments")) mountLibrary(libraryRoot);
    if (jobsLink) root.querySelector("#document-status").value = library.status;
    if (!await refreshBatches()) return;
    const link = pendingDeepLink;
    if (link && link.route === location.hash) {
      // Consume deep links once; polling and closing a dialog must not reopen it.
      pendingDeepLink = null;
      // Not the bare #documents, which now opens Assignments: a reload keeps this list.
      history.replaceState(history.state, "", `#documents?tab=${link.jobs || link.batchId ? "batches" : "assignments"}`);
      route = location.hash;
      setHeader();
      try {
        // #documents?create=1 (Home's quick action, the command palette) opens Create assignment once.
        if (link.create) await wizard.open({definitionId: library.course});
        else if (link.quiz) await quizDrafts.openCreate(library.course);  // #documents?quiz=new (the command palette)
        else if (link.chartId) await wizard.open({chartId: link.chartId});
        else if (link.assignmentId) {
          const assignment = catalog.assignments.find(item => item.id === link.assignmentId);
          if (!assignment) throw new Error("That assignment is no longer listed.");
          await wizard.openForAssignment(assignment, link.step);
        }
        else if (link.jobs) showJobs(link.jobs);  // #documents?jobs=<assignment> (the Exams hub's Print stage, a quiz's page): its print jobs
        else { library.tab = "batches"; drawLibrary(); await detail(link.batchId); }
      } catch (cause) { toast(cause.message, true); }
    }
  }

  function mountLibrary(root) {
    // The creation entry is the page header's primary action (and #documents?create=1), so the button name stays unique.
    root.innerHTML = `<div id="document-activity" class="document-activity" hidden></div>
      <div class="document-library-nav"><div class="document-tabs" role="tablist" aria-label="Assignments and print jobs"><button type="button" id="document-tab-assignments" role="tab" aria-selected="true" aria-label="Assignments" aria-controls="document-library-assignments" data-document-tab="assignments">${pictogram("paper")}Assignments <span data-count="assignments">0</span></button><button type="button" id="document-tab-batches" role="tab" aria-selected="false" tabindex="-1" aria-label="Print jobs" aria-controls="document-library-batches" data-document-tab="batches">${pictogram("print")}Print jobs <span data-count="batches">0</span></button></div></div>
      <div class="document-filters"><label class="field document-search">Search<span class="document-search-control">${pictogram("search")}<input id="document-search" type="search" data-page-search aria-label="Search assignments and print jobs" placeholder="Find an assignment, version, or section…"></span></label><label class="field">Course<select id="document-course" aria-label="Filter by course"><option value="">All courses</option></select></label><label class="field" id="document-status-field" hidden>Status<select id="document-status" aria-label="Filter print jobs"><option value="current">Current</option><option value="all">All statuses</option><option value="ready">Ready to print</option><option value="working">In progress</option><option value="attention">Needs attention</option><option value="history">Cancelled & superseded</option><option value="archived">Archived (grades certified)</option></select></label></div>
      <div class="document-results-heading"><p id="document-results" role="status"></p><button type="button" class="text-button" id="document-clear" hidden>Clear filters</button></div>
      <div id="document-library-error" class="callout document-library-error" data-tone="danger" role="alert" hidden><span></span><button type="button" class="text-button">Try again</button></div>
      <section id="document-library-assignments" role="tabpanel" aria-labelledby="document-tab-assignments"><div id="document-quiz-drafts"></div><div id="document-assignments" class="document-assignment-grid"><p class="loading-state">Loading assignments…</p></div></section>
      <section id="document-library-batches" role="tabpanel" aria-labelledby="document-tab-batches" hidden><div id="document-batches" class="document-export-list"></div></section>
      <nav class="document-pagination" aria-label="Results pages" hidden><span id="document-page-number" role="status"></span><div><button type="button" class="button" id="document-previous" aria-label="Previous results">← Previous</button><button type="button" class="button" id="document-next" aria-label="Next results">Next →</button></div></nav>`;
    root.querySelector("#document-search").value = library.query;
    root.querySelector("#document-status").value = library.status;
    root.querySelector("#document-search").oninput = event => { library.query = event.target.value; library.page = 1; drawLibrary(); };
    root.querySelector("#document-course").onchange = event => { library.course = event.target.value; library.assignment = ""; library.page = 1; drawLibrary(); };
    root.querySelector("#document-status").onchange = event => { library.status = event.target.value; library.page = 1; drawLibrary(); };
    root.querySelector("#document-clear").onclick = clearFilters;
    root.querySelector("#document-library-error button").onclick = () => render().catch(error => toast(error.message, true));
    root.querySelector("#document-previous").onclick = () => { library.page--; drawLibrary(); };
    root.querySelector("#document-next").onclick = () => { library.page++; drawLibrary(); };
    const tabs = [...root.querySelectorAll("[data-document-tab]")];
    tabs.forEach(button => {
      button.onclick = () => { library.tab = button.dataset.documentTab; library.page = 1; drawLibrary(); };
      button.onkeydown = event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[1] : tabs[1 - tabs.indexOf(button)];
        next.click(); next.focus();
      };
    });
  }

  function clearFilters() {
    Object.assign(library, {query: "", course: "", status: "current", assignment: "", page: 1});
    const root = document.querySelector("#documents-section");
    root.querySelector("#document-search").value = "";
    root.querySelector("#document-status").value = "current";
    drawLibrary(); root.querySelector("#document-search").focus();
  }

  // One assignment's print jobs on the Print jobs tab (a card's "N print jobs", #documents?jobs=<assignment>): its current jobs, or
  // its archived ones when none is current (`jobsOf`). Status, search and page start over.
  function showJobs(assignmentId) {
    const assignment = catalog.assignments.find(a => a.id === assignmentId);
    if (!assignment) throw new Error("That assignment is no longer listed.");
    const root = document.querySelector("#documents-section");
    const {status} = jobsOf(assignmentId, libraryJobs());
    // A course filter left on another course would hide every one of them.
    const course = library.course && library.course !== assignment.definition_id ? "" : library.course;
    Object.assign(library, {tab: "batches", assignment: assignmentId, query: "", course, status, page: 1});
    root.querySelector("#document-search").value = "";
    root.querySelector("#document-status").value = status;
    drawLibrary();
  }

  // Print jobs are listed by export: one confirmation of a seating chart (a room's sections) or one Create print jobs click
  // shares a request prefix, so its section jobs sit under one header with the files to print. Jobs issued by first-page
  // registration of scanned papers are never printed; they sit apart, folded. Jobs printed for the term as one stack in several
  // versions (a drafted quiz's approval: one job per version, one request) are ONE group whose print file interleaves the versions
  // (requirement, 2026-10-01).
  const groupKey = b => b.origin === "registration" ? "registration"
    : !b.section_id && b.origin === "section" && b.version ? `${b.origin}|${b.export || b.id}|assignment:${b.version.assignment_id}`
    : `${b.origin}|${b.export || b.id}|${b.assignment_version_id}`;
  function groupsOf(list) {
    const found = new Map();
    list.forEach(job => { const key = groupKey(job); if (!found.has(key)) found.set(key, []); found.get(key).push(job); });
    return [...found].map(([key, items]) => ({key, items: items.sort((x, y) => (x.section?.number ?? 0) - (y.section?.number ?? 0) || String(x.version?.label ?? "").localeCompare(String(y.version?.label ?? "")) || x.created_at.localeCompare(y.created_at)),
      newest: items.reduce((latest, item) => item.created_at > latest ? item.created_at : latest, "")}))
      .sort((x, y) => (x.key === "registration") - (y.key === "registration") || y.newest.localeCompare(x.newest));
  }
  function groupState(items) {
    const states = items.map(item => item.state);
    if (states.includes("failed")) return "failed";
    const busy = states.find(state => working.has(state));
    if (busy) return busy;
    if (states.includes("ready")) return "ready";
    return states.every(state => state === "cancelled") ? "cancelled" : "superseded";
  }
  const stateChip = state => `<span class="chip document-status-chip" data-tone="${statusTones[state] || "neutral"}" data-status="${escape(state)}">${escape(statusNames[state] || state)}</span>`;

  // `versioned`: a row of a stack printed in several versions names its version; the group's header names the sections.
  function jobRow(b, registration = false, versioned = false) {
    const title = registration
      ? `<span class="document-course-code">${escape(b.assignment?.code || b.section?.code || "Course")}</span><strong>${escape(b.assignment?.title || "Retained assignment")}</strong><small>${escape(b.section ? `Section ${b.section.number} · ${termText(b.section)}` : "Retained section")}</small>`
      : versioned ? `<strong>${escape(versionText(b.version))}</strong><small>${escape(termText(b.termRow))}</small>`
      : `<strong>${escape(b.section ? `Section ${b.section.number}` : b.section_id ? "Retained section" : stackPlace(b.sections))}</strong><small>${escape(termText(b.termRow))}</small>`;
    const status = registration && b.state === "ready" ? '<span class="chip" data-tone="neutral">Not for printing</span>' : `<span class="document-status" data-status="${escape(b.state)}">${escape(statusNames[b.state] || b.state)}</span>`;
    return `<button type="button" class="document-job" data-batch="${escape(b.id)}"><span class="document-job-title">${title}</span><span class="document-job-quantity"><strong>${b.quantity}</strong><small>${b.quantity === 1 ? "copy" : "copies"}</small></span><span class="document-job-status">${status}<small>${escape(new Date(b.created_at).toLocaleDateString(undefined, {month: "short", day: "numeric", year: "numeric"}))}</small></span><span class="document-job-open">${pictogram("arrow")}</span></button>`;
  }

  function groupMarkup(group, siblings) {
    if (group.key === "registration") {
      const key = "documents:registration-jobs";
      const open = rememberedOpen(key);
      return `<details class="disclosure document-registration-group" data-registration-jobs data-disclosure-key="${key}"${open ? " open" : ""}><summary><span>Issued for scanned papers <span class="chip" data-tone="neutral">Not for printing</span></span><span class="disclosure-hint">${group.items.length}</span></summary><div class="disclosure-body"><p class="supporting-copy">Labels issued when scanned papers printed without labels were matched to students. Nothing here needs printing.</p><div class="document-export-rows">${group.items.map(item => jobRow(item, true)).join("")}</div></div></details>`;
    }
    const first = group.items[0];
    const all = siblings.get(group.key) || group.items;
    const numbers = [...new Set(all.flatMap(item => item.section_id ? [item.section?.number] : (item.sections || []).map(s => s.number)).filter(n => n !== undefined))].sort((x, y) => x - y);
    const copies = all.reduce((sum, item) => sum + item.quantity, 0);
    // Copies that came back in scans still in the Inbox (task 13); shown once any has.
    const scanned = all.reduce((sum, item) => sum + (item.scanned || 0), 0);
    // A one-job group's row already names its section, copies and date; its header adds only what the row does not say.
    const single = all.length === 1;
    // Several versions in one group: a stack printed for the term, one job per version, printed as one interleaved file.
    const labels = [...new Set(all.map(item => item.version?.label ?? ""))].sort();
    const stacked = new Set(all.map(item => item.assignment_version_id)).size > 1;
    const factList = [first.room_name, single ? "" : numbers.length ? `Section${numbers.length === 1 ? "" : "s"} ${numbers.join(", ")}` : "", single ? "" : plural(copies, "copy", "copies"),
      stacked ? `Versions ${labels.join(", ")} interleaved` : versionText(first.version),
      single ? "" : shortDate(group.newest), scanned ? `Scanned ${scanned.toLocaleString()}/${copies.toLocaleString()}` : ""].filter(Boolean);
    const facts = factList.join(" · ");
    // Each fact stays on one line (a date never breaks across two).
    const factsMarkup = factList.map(fact => `<span class="document-export-fact">${escape(fact)}</span>`).join(" · ");
    // The group's state only where its rows differ, or where the filter hides some of its jobs; otherwise each row says it.
    const showState = all.length > 1 && (new Set(all.map(item => item.state)).size > 1 || all.length !== group.items.length);
    const ready = all.filter(downloadable);
    const files = first.origin === "seating" && first.seating_chart_id && all.length > 1
      ? exportFiles(first.seating_chart_id, all.map(item => ({...item, section_number: item.section?.number})), {compact: true})
      : stacked ? stackFiles(all, {compact: true})
      : all.length === 1 ? jobFiles(first, {compact: true})
      : ready.map(item => `<span class="document-export-section-files"><span>${escape(placeOf(item))}</span>${jobFiles(item, {compact: true})}</span>`).join("");
    return `<section class="document-export" data-export-group="${escape(group.key)}" aria-label="${escape([first.assignment?.title || "Retained assignment", single && first.section ? `Section ${first.section.number}` : "", facts].filter(Boolean).join(" · "))}"><header class="document-export-head"><div class="document-export-title"><span class="document-course-code">${escape(first.assignment?.code || first.section?.code || "Course")}</span><h3>${escape(first.assignment?.title || "Retained assignment")}</h3><p class="document-export-facts">${factsMarkup}</p></div><div class="document-export-side">${showState ? stateChip(groupState(all)) : ""}${files}</div></header><div class="document-export-rows">${group.items.map(item => jobRow(item, false, stacked)).join("")}</div></section>`;
  }

  // ----- Assignment cards: the life of one assignment in its course's current term (task 11) -----------------------------
  // Paper, Seating and Print are the server's derived stages (the shared rule the exam hub uses); each stage opens the
  // assignment window at its step, and the footer's one primary action is the next unfinished stage. Nothing is decided here.
  const STAGE_NAMES = {paper: "Paper", seating: "Seating", print: "Print"};
  const STAGE_STEPS = {paper: "version", seating: "seating", print: "print"};
  const NEXT_LABELS = {paper: {idle: "Add paper"}, seating: {idle: "Seating", started: "Finish seating"}, print: {idle: "Print", started: "Printing", failed: "Fix printing"}};
  const stepHref = (a, step) => `#documents?assignment=${encodeURIComponent(a.id)}&step=${step}`;
  function lifecycleOf(a) {
    const rows = (catalog.lifecycle || []).filter(row => row.assignment_id === a.id);
    return rows.find(row => row.active) || rows[0] || null;
  }
  function paperText(versions) {
    const pdfs = versions.filter(v => !isStampsOnly(v)).map(v => v.label);
    const shown = pdfs.length > 3 ? `${pdfs.slice(0, 3).join(", ")} +${pdfs.length - 3}` : pdfs.join(", ");
    return [pdfs.length ? `PDF ${shown}` : "", versions.some(isStampsOnly) ? "Stamps only" : ""].filter(Boolean).join(" · ");
  }
  function assignmentCard(a, jobs) {
    const versions = catalog.versions.filter(v => v.assignment_id === a.id);
    const life = lifecycleOf(a);
    const stages = life?.stages || {paper: versions.length ? "done" : "idle", seating: "idle", print: "idle"};
    // The paper names the versions its current print jobs print (a rebuilt quiz's A2–D2, not the replaced A–D), else every version.
    const printedVersions = (life?.printed_versions || []).length ? versions.filter(v => life.printed_versions.includes(v.label)) : [];
    const paper = paperText(printedVersions.length ? printedVersions : versions);
    const charts = life?.charts || 0;
    // What was printed: print jobs only, never registration, superseded or cancelled ones.
    const own = jobs.filter(b => b.assignment?.id === a.id && b.origin !== "registration" && !endedJob(b));
    // Printed for the term as one stack (TERM PRINT JOBS: no job names a section; ONE STACK, EVERY VERSION when several versions).
    const termJobs = own.filter(b => b.term_id === life?.term_id);
    const oneStack = Boolean(life?.printed_stack) || (termJobs.length > 0 && termJobs.every(b => !b.section_id));
    const printed = [life?.last_printed_at ? `Printed ${shortDate(life.last_printed_at)}` : "Printed", life?.printed_copies ? plural(life.printed_copies, "copy", "copies") : "",
      oneStack ? "one stack" : charts ? plural(charts, "room") : ""].filter(Boolean).join(" · ");
    const details = {
      paper: stages.paper === "done" ? paper : "No paper registered yet",
      seating: {done: charts ? `${plural(charts, "room")} confirmed` : oneStack ? "No seating chart · printed as one stack" : "No seating chart · printed by section",
        started: `${life?.confirmed_charts || 0} of ${plural(charts, "chart")} confirmed`, idle: "Not seated"}[stages.seating] || "",
      print: {done: printed, started: "Print jobs being prepared", failed: "A print job needs attention", idle: "Nothing printed yet"}[stages.print] || "",
    };
    const order = ["paper", "seating", "print"];
    // Once scanned papers are linked to the assignment (and its exam date is not still ahead), preparation is over: a stage
    // left unused (a quiz printed without labels has no seating or print jobs) is not the next thing, only a failure is.
    const collected = Boolean(life?.collected);
    const next = order.find(key => stages[key] === "failed") || (collected ? null : order.find(key => stages[key] !== "done")) || null;
    const untouched = order.every(key => stages[key] === "idle");
    const steps = order.map(key => ({key, label: STAGE_NAMES[key], title: `${STAGE_NAMES[key]} · ${a.title}`, detail: details[key],
      done: stages[key] === "done", failed: stages[key] === "failed", started: stages[key] === "started", current: key === next && !untouched,
      destination: stepHref(a, STAGE_STEPS[key])}));
    // "N print jobs" counts the list its button opens (`jobsOf`, the counting rule above).
    const mine = jobsOf(a.id, jobs).jobs.length;
    // The stage that matters now, in words a phone shows too (the step graph says it only on hover): a failed one, else the next,
    // else the last one done. Not for an untouched card, nor where it would repeat the paper fact.
    const stageKey = order.find(key => stages[key] === "failed") || next || [...order].reverse().find(key => stages[key] === "done") || null;
    const stageLine = stageKey && !untouched && !(stageKey === "paper" && stages.paper === "done") ? `${STAGE_NAMES[stageKey]} · ${details[stageKey]}` : "";
    const facts = [life?.exam_date ? `Exam ${wallDate(life.exam_date)}${life.exam_time ? ` · ${life.exam_time}` : ""}` : "", paper || "No paper yet",
      // What was printed, when, how many: in the facts unless the stage line already says it.
      stages.print === "done" && stageKey !== "print" ? printed : "", life ? life.term_label : "No current term"].filter(Boolean);
    // An approved quiz's plan is on Grading before any paper exists (grading_entry_plan_id); otherwise once it holds responses.
    const gradingPlan = life?.grading_entry_plan_id || life?.grading_plan_id;
    const grading = gradingPlan ? `#grading/${encodeURIComponent(gradingPlan)}` : "";
    const exam = life?.exam_event_id ? `#exams/${encodeURIComponent(life.exam_event_id)}`
      : life?.active ? `#exams/assessment/${encodeURIComponent(a.id)}/${encodeURIComponent(life.term_id)}` : "";
    const continueLabel = `Continue ${a.code} · ${a.title}`;
    // Collected papers take the primary only where an unused preparation stage would otherwise claim it (a finished card keeps its own).
    const skipped = collected && !next && order.some(key => stages[key] !== "done");
    const examPrimary = skipped && !grading && exam;
    const primary = skipped && grading ? `<a class="button primary" href="${escape(grading)}">Grading →</a>`
      : examPrimary ? `<a class="button primary" href="${escape(exam)}">Exam →</a>`
      : untouched && !collected
      ? `<button type="button" class="button primary" data-assignment="${escape(a.id)}" aria-label="${escape(continueLabel)}">Continue</button>`
      : next ? `<a class="button primary" href="${escape(stepHref(a, STAGE_STEPS[next]))}" data-assignment-next="${escape(next)}">${escape(NEXT_LABELS[next][stages[next]] || STAGE_NAMES[next])} →</a>`
      : grading ? `<a class="button primary" href="${escape(grading)}">Grading →</a>` : "";
    const canvas = life ? canvasStatus.get(life.term_id)?.data : null;
    const {onCanvas, fact: canvasFact} = canvasStanding(canvas, a.id);
    if (canvasFact) facts.push(canvasFact);
    const owed = life?.exam_event_id ? makeupsOwed.counts.get(life.exam_event_id) || 0 : 0;
    const links = [
      untouched && !collected ? "" : `<button type="button" class="text-button" data-assignment="${escape(a.id)}" aria-label="${escape(continueLabel)}">Continue</button>`,
      canvas?.configured ? `<button type="button" class="text-button" data-assignment-canvas="${escape(a.id)}" data-term="${escape(life.term_id)}" aria-label="${escape(`${onCanvas ? "Update" : "Put"} ${a.code} · ${a.title} on Canvas`)}">${onCanvas ? "Update on Canvas…" : "Put on Canvas…"}</button>` : "",
      exam && !examPrimary ? `<a class="link-arrow" href="${escape(exam)}">Exams page</a>` : "",
      grading && next ? `<a class="link-arrow" href="${escape(grading)}">Grading</a>` : "",
      // The approved quiz's own page: its interleaved file, rebuild and Put it on Canvas are there.
      life?.quiz_draft_id ? `<a class="link-arrow" href="#documents/quiz/${encodeURIComponent(life.quiz_draft_id)}">Quiz draft</a>` : "",
      // 'Given on' on the card itself (requirement, 2026-10-03), for the card's current term: the date the facts show as "Exam <date>".
      life ? `<button type="button" class="text-button" data-assignment-given-on="${escape(a.id)}" data-term="${escape(life.term_id)}" aria-label="${escape(`Given on date for ${a.code} · ${a.title}`)}">Given on…</button>` : "",
      `<button type="button" class="text-button" data-assignment-rename="${escape(a.id)}" aria-label="${escape(`Rename ${a.code} · ${a.title}`)}">Rename…</button>`,
      // Makeups (requirement, 2026-10-03): who owes one (the Absences page's Makeups view), and a makeup version drafted by a model.
      owed ? `<a class="link-arrow" href="#absences?view=makeups">${plural(owed, "makeup")} owed</a>` : "",
      gradingPlan ? `<button type="button" class="text-button" data-assignment-makeup="${escape(gradingPlan)}" data-title="${escape(`${a.code} · ${a.title}`)}">Draft makeup version…</button>` : "",
    ].filter(Boolean).join("");
    // Set up grading now (requirement, 2026-10-02), for the card's current term while its exam record has no grading plan at all.
    const menu = life && !life.has_grading_plan && !gradingPlan ? actionMenuMarkup({id: `assignment-actions-${a.id}-${life.term_id}`,
      label: `More actions for ${a.code} · ${a.title}`, items: [{label: "Set up grading now", data: {assignmentSetUpGrading: a.id, term: life.term_id}}]}) : "";
    return `<article class="card document-assignment-card" data-assignment-card="${escape(a.id)}"><header class="card-head"><span class="document-course-code">${escape(a.code)}</span><h3 title="${escape(`Key: ${a.assignment_key}`)}">${escape(a.title)}</h3><p class="document-card-facts">${escape(facts.join(" · "))}${mine ? ` · <button type="button" class="text-button" data-assignment-jobs="${escape(a.id)}">${plural(mine, "print job")}</button>` : ""}</p></header>
      <div class="card-body document-card-stages" data-size="compact">${pipelineMarkup(steps, {e: escape}, null, `${a.title} stages`)}${stageLine ? `<p class="supporting-copy document-card-stage">${escape(stageLine)}</p>` : ""}</div>
      <footer class="card-foot">${primary}${links ? `<span class="document-card-links">${links}</span>` : ""}${menu}</footer></article>`;
  }

  // Set up grading now (requirement, 2026-10-02): the term's grading plan before any paper (POST /api/grading/assignments, which makes the
  // term's exam record when it has none), then its Rubrics & Templates step.
  async function setUpGrading(button, a, termId) {
    if (!a || !termId) return;
    const from = location.hash;
    setBusy(button, true);
    try {
      const plan = await api("/api/grading/assignments", "POST", {assignment_id: a.id, term_id: termId}, {}, 180000);
      if (location.hash === from) location.hash = `#grading/${encodeURIComponent(plan.id)}?tab=rubrics`;
      toast(...setUpGradingNotice(a.title, plan));
    } catch (cause) {
      toast(cause.message, true);
    } finally {
      setBusy(button, false);
    }
  }

  // Renaming (document_identity rename_assignment; requirement, 2026-09-27). The assignment stays the
  // same; the title changes everywhere it is shown, with the earlier one kept in its history. Printed papers keep their text.
  function renameDialog(a, onChanged = () => render(true)) {
    if (!a) return;
    let requestId = crypto.randomUUID(), requestedTitle = "";
    showDialog(`<form id="assignment-rename-form">${dialogHeader("Rename assignment", `${escape(a.code)} · ${escape(a.title)}`)}<div class="dialog-body">
      <label class="field">Title<input name="title" required maxlength="240" value="${escape(a.title)}" autocomplete="off"></label>
      <p class="field-hint">Every page shows the new name, and anything made from now on uses it (cards, print jobs, rosters, gradebook columns, Gradescope assignments Faculty creates). Papers already printed, mail already sent and other records keep the name they were made with. The earlier name is kept in the assignment’s history, and DR bookings still match “Test” and “Exam” alike.</p>
      <p id="form-error" class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Rename</button></footer></form>`);
    const form = document.querySelector("#assignment-rename-form");
    const current = dialogSession(form, dialog()), from = location.hash;
    const input = form.elements.title, error = form.querySelector("#form-error"), submit = form.querySelector('[type="submit"]');
    input.select();
    form.onsubmit = async event => {
      event.preventDefault();
      if (!current() || submit.disabled) return;
      const title = input.value.trim();
      if (!title) return;
      if (requestedTitle && requestedTitle !== title) requestId = crypto.randomUUID();
      requestedTitle = title;
      if (title === a.title) { document.querySelector("#dialog").close(); return; }
      setBusy(submit, true); error.hidden = true;
      try {
        await api(`/api/documents/assignments/${encodeURIComponent(a.id)}/rename`, "POST", {title, request_id: requestId});
        if (current()) dialog().close();
        toast(`Renamed to ${title}.`);
        if (location.hash === from) await onChanged();
        void refresh?.();
      } catch (cause) {
        error.textContent = cause.message; error.hidden = false; setBusy(submit, false);
      }
    };
  }

  // 'Given on' on the card (requirement, 2026-10-03), as Create assignment's Details
  // saves it: the term's exam record's main sitting, through the Exams page's own routes (POST /api/exams when the term has no
  // record, for the sections ticked here, then its sittings route), so Home's Coming up, the Calendar and its Exams page show it.
  // The main sitting the card's date comes from is changed; an empty field takes the date off it. A date a seating chart records
  // is named instead, with a link to that chart.
  async function givenOnDialog(button, a, termId, onChanged = () => render(true)) {
    const intended = opening();
    const life = (catalog.lifecycle || []).find(row => row.assignment_id === a?.id && row.term_id === termId);
    if (!a || !life) return;
    const subtitle = `${escape(a.code)} · ${escape(a.title)} · ${escape(life.term_label)}`;
    if (life.exam_date_chart_id) {
      showDialog(`<form id="assignment-given-on-form">${dialogHeader("Given on", subtitle)}<div class="dialog-body">
        <p class="supporting-copy">Given on ${escape(wallDate(life.exam_date))}${life.exam_time ? ` · ${escape(life.exam_time)}` : ""}, as its seating chart records. Change it on the chart.</p></div>
        <footer class="dialog-footer"><button type="button" class="button" data-action="close">Close</button><a class="button primary" href="#seating/${encodeURIComponent(life.exam_date_chart_id)}">Open the seating chart</a></footer></form>`);
      document.querySelector('#assignment-given-on-form a[href^="#seating/"]').onclick = () => document.querySelector("#dialog").close();
      return;
    }
    const find = async () => (await api("/api/exams?summary=0")).exams.find(item => item.assignment_id === a.id && item.term_id === termId);
    const mainOf = exam => {
      const mains = (exam?.sittings || []).filter(item => item.kind === "main");
      return mains.filter(item => item.date).sort((x, y) => x.date.localeCompare(y.date) || x.id.localeCompare(y.id))[0] || mains[0] || null;
    };
    let exam = null, planned = [];
    setBusy(button, true);
    try {
      // The term's planned rows no record follows yet (SCHEDULING "CLASS SCHEDULE"): the one this assignment stands for gives its date.
      planned = await openRows(api, termId, "given");
      const listed = await find();
      exam = listed ? (listed.sittings ? listed : await api(`/api/exams/${encodeURIComponent(listed.id)}`)) : null;
    } catch (cause) {
      toast(cause.message, true);
      return;
    } finally {
      setBusy(button, false);
    }
    if (!intended()) return;
    const current = mainOf(exam)?.date || "";
    const sections = (catalog.sections || []).filter(s => s.term_id === termId).sort((x, y) => x.number - y.number);
    const sectionsMarkup = exam ? "" : `<fieldset class="seating-sections"><legend>Sections it is given to</legend><p class="supporting-copy">Its exam record is made for these sections.</p><div class="detail-group">${sections.map(s => `<label class="checkbox-field"><input type="checkbox" name="section" value="${escape(s.id)}" checked>Section ${escape(String(s.number))}</label>`).join("")}</div></fieldset>`;
    showDialog(`<form id="assignment-given-on-form">${dialogHeader("Given on", subtitle)}<div class="dialog-body">
      <label class="field">Given on<input type="date" name="given_on" value="${escape(current)}"></label>
      <p class="field-hint">The campus date it is given: it puts the assignment on Home’s Coming up, the Calendar and its Exams page, as its main sitting.${current ? " Clear the field to take the date off." : ""}</p>
      ${sectionsMarkup}
      <p id="form-error" class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Save</button></footer></form>`);
    const form = document.querySelector("#assignment-given-on-form");
    const active = dialogSession(form, dialog()), from = location.hash;
    const error = form.querySelector("#form-error"), submit = form.querySelector('[type="submit"]');
    const fail = message => { error.textContent = message; error.hidden = false; setBusy(submit, false); };
    const prefill = attachPrefill({ rows: planned, before: form.elements.given_on.closest("label"), dateInput: form.elements.given_on,
      title: () => a.title, sections: () => [...form.querySelectorAll('input[name="section"]:checked')].map(input => input.value), escape });
    form.onsubmit = async event => {
      event.preventDefault();
      if (!active() || submit.disabled) return;
      // The date input's own YYYY-MM-DD, kept exactly as typed (a campus wall-clock date, never converted through a time zone).
      const day = form.elements.given_on.value.trim();
      if (day && !/^\d{4}-\d{2}-\d{2}$/.test(day)) return fail("Enter the date it is given as YYYY-MM-DD, or leave it empty.");
      // The planned row chosen follows this assignment from here on, whether or not the date changes.
      const plannedItem = prefill.chosen();
      const follow = () => plannedItem ? followRecord(api, termId, plannedItem, {assignment_id: a.id}) : null;
      if (day === current) {
        if (!plannedItem) { dialog().close(); return; }  // nothing changed, nothing to follow
        setBusy(submit, true);
        try { await follow(); } catch (cause) { if (active()) fail(cause.message || "That could not be saved."); else toast(cause.message, true); return; }
        if (active()) dialog().close();
        if (location.hash === from) await onChanged();
        return;
      }
      const sectionIds = [...form.querySelectorAll('input[name="section"]:checked')].map(input => input.value);
      if (!exam && !sectionIds.length) return fail("Tick at least one section it is given to.");
      setBusy(submit, true); error.hidden = true;
      try {
        if (!exam) {
          try {
            exam = await api("/api/exams", "POST", {term_id: termId, assignment_id: a.id, title: a.title, section_ids: sectionIds});
          } catch (cause) {
            const listed = cause.status === 409 ? await find() : null;  // another tab made the record meanwhile
            if (!listed) throw cause;
            exam = await api(`/api/exams/${encodeURIComponent(listed.id)}`);
          }
        }
        if (!active()) return;
        const target = mainOf(exam);
        const base = `/api/exams/${encodeURIComponent(exam.id)}/sittings`;
        if (target) await api(`${base}/${encodeURIComponent(target.id)}`, "PATCH", {version: target.version, date: day});
        else await api(base, "POST", {version: exam.version, label: exam.title, kind: "main", date: day, time: "", location: "", room_id: null});
        await follow();
        if (active()) dialog().close();
        toast(day ? `${a.title}: given on ${wallDate(day)}.` : `${a.title}: the date is taken off its main sitting.`);
        if (location.hash === from) await onChanged();
        void refresh?.();
      } catch (cause) {
        fail(cause.message);
      }
    };
  }

  function drawLibrary() {
    if (!catalog) return;
    const root = document.querySelector("#documents-section");
    if (root.hidden || !root.querySelector("#document-library-root")) return;
    const jobsView = library.tab === "batches";
    const assignments = new Map(catalog.assignments.map(a => [a.id, a]));
    const jobs = libraryJobs();
    // Index once for this draw instead of filtering every version and job again in each sort comparison.
    const versionsByAssignment = new Map(), lifecycleByAssignment = new Map(), latestByAssignment = new Map();
    const rememberActivity = (id, time) => { if (time && time > (latestByAssignment.get(id) || "")) latestByAssignment.set(id, time); };
    for (const a of catalog.assignments) rememberActivity(a.id, a.created_at);
    for (const version of catalog.versions) {
      if (!versionsByAssignment.has(version.assignment_id)) versionsByAssignment.set(version.assignment_id, []);
      versionsByAssignment.get(version.assignment_id).push(version);
      rememberActivity(version.assignment_id, version.created_at);
    }
    for (const job of jobs) rememberActivity(job.assignment?.id, job.created_at);
    for (const row of catalog.lifecycle || []) {
      if (!lifecycleByAssignment.has(row.assignment_id)) lifecycleByAssignment.set(row.assignment_id, []);
      lifecycleByAssignment.get(row.assignment_id).push(row);
    }
    for (const [id, rows] of lifecycleByAssignment) rememberActivity(id, (rows.find(row => row.active) || rows[0])?.last_printed_at);
    const terms = library.query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
    const matches = text => terms.every(term => text.toLocaleLowerCase().includes(term));
    const matchingAssignments = catalog.assignments.filter(a => (!library.course || a.definition_id === library.course) && matches(`${a.code} ${a.title} ${a.assignment_key} ${(versionsByAssignment.get(a.id) || []).map(v => v.label).join(" ")}`)).sort((a, b) => (latestByAssignment.get(b.id) || "").localeCompare(latestByAssignment.get(a.id) || "") || b.created_at.localeCompare(a.created_at));
    // Archived (a view, certified_archive.py): an assignment whose every current term is certified folds under "Archived"; its jobs
    // leave every status but their own (`statusMatches`). Current (the default) is the counting rule's list.
    const archivedAssignment = a => { const rows = lifecycleByAssignment.get(a.id) || []; return rows.length > 0 && rows.every(row => row.archived); };
    const filteredJobs = jobs.filter(b => (!library.course || (b.assignment?.definition_id || b.section?.definition_id) === library.course) && (!library.assignment || b.assignment?.id === library.assignment) && statusMatches(b, library.status) && matches(`${b.assignment?.code || ""} ${b.assignment?.title || ""} ${b.assignment?.assignment_key || ""} ${b.version?.label || ""} ${b.section ? sectionLabel(b.section) : ""} ${b.room_name || ""}`));
    const filteredAssignments = matchingAssignments.filter(a => !archivedAssignment(a));
    const archivedAssignments = matchingAssignments.filter(archivedAssignment);
    const groups = groupsOf(filteredJobs);
    const siblings = new Map(groupsOf(jobs).map(group => [group.key, group.items]));
    const items = jobsView ? groups : filteredAssignments;
    const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
    library.page = Math.max(1, Math.min(library.page, pages));
    const offset = (library.page - 1) * PAGE_SIZE;
    const courseOptions = '<option value="">All courses</option>' + catalog.definitions.map(d => `<option value="${escape(d.id)}">${escape(d.code)}</option>`).join("");
    const course = root.querySelector("#document-course");
    if (course.innerHTML !== courseOptions) course.innerHTML = courseOptions;
    course.value = library.course;
    root.querySelectorAll("[data-document-tab]").forEach(button => {
      const active = button.dataset.documentTab === library.tab;
      button.setAttribute("aria-selected", String(active)); button.tabIndex = active ? 0 : -1;
    });
    // The badges count what each tab's default list shows (the counting rule above), not the catalog's every row.
    root.querySelector('[data-count="assignments"]').textContent = catalog.assignments.filter(a => !archivedAssignment(a)).length;
    root.querySelector('[data-count="batches"]').textContent = jobs.filter(countedJob).length;
    const pending = jobs.filter(b => working.has(b.state) && !archivedJob(b)).length, failed = jobs.filter(b => b.state === "failed" && !archivedJob(b)).length;
    // One attention line above the tabs: a quiz draft waiting for the instructor's review (a link to it), then failed jobs (a
    // button that filters to them), then jobs still being prepared.
    const ready = drafts.filter(d => d.state === "ready" && !d.scrapped);
    const draftLink = !ready.length ? "" : `<a class="text-button" href="#documents/quiz/${encodeURIComponent(ready[0].id)}" data-ready-draft>${escape(ready.length === 1 ? `${ready[0].title} draft is ready for you` : `${ready.length} quiz drafts are ready for you`)} · Open</a>`;
    const activity = draftLink || failed || pending ? `<div class="callout document-attention" data-tone="${failed ? "danger" : "info"}" role="${failed ? "alert" : "status"}">${draftLink}${failed ? `<button type="button" class="text-button" data-show-attention>${failed} ${failed === 1 ? "job needs" : "jobs need"} attention · Show</button>` : ""}${pending ? `<span>${plural(pending, "print job")} being prepared</span>` : ""}</div>` : "";
    const activityHost = root.querySelector("#document-activity");
    if (renderedResults.get(activityHost) !== activity) { activityHost.innerHTML = activity; renderedResults.set(activityHost, activity); }
    activityHost.hidden = !activity;
    root.querySelector("#document-status-field").hidden = !jobsView;
    root.querySelector("#document-library-assignments").hidden = jobsView;
    root.querySelector("#document-library-batches").hidden = !jobsView;
    const scoped = assignments.get(library.assignment);
    // Jobs issued for scanned papers are folded apart with their own count; the line counts print jobs (the counting rule).
    const printGroups = groups.filter(group => group.key !== "registration");
    root.querySelector("#document-results").textContent = jobsView
      ? `${plural(filteredJobs.filter(b => b.origin !== "registration").length, "print job")}${scoped ? ` for ${scoped.title}` : ""} in ${plural(printGroups.length, "group")} · Newest first`
      : `${items.length} assignment${items.length === 1 ? "" : "s"}${!library.query ? " · Shared across course terms" : ""}`;
    root.querySelector("#document-clear").hidden = !(library.query || library.course || (jobsView && (library.status !== "current" || library.assignment)));
    const empty = (type, all) => all
      ? emptyMarkup({icon: type === "assignments" ? "paper" : "print", title: type === "assignments" ? "No assignments yet" : "No print jobs yet",
          body: type === "assignments" ? "Create an assignment to seat it, add its paper and print labelled copies." : "Continue an assignment to print copies for its sections."})
      : emptyMarkup({kind: "filtered", title: `No ${type === "assignments" ? "assignments" : "print jobs"} match these filters`, body: "Try a different search, or clear the filters.",
          action: {label: "Clear filters", data: {"clear-filters": ""}}});
    const archivedMarkup = archivedAssignments.length ? `<details class="disclosure document-archived" data-document-archived ${root.querySelector("[data-document-archived]")?.open ? "open" : ""}><summary><span>Archived · grades certified</span><span class="disclosure-hint">${archivedAssignments.length}</span></summary><div class="disclosure-body document-assignment-grid">${archivedAssignments.map(a => assignmentCard(a, jobs)).join("")}</div></details>` : "";
    const assignmentMarkup = (filteredAssignments.slice(jobsView ? 0 : offset, (jobsView ? 0 : offset) + PAGE_SIZE).map(a => assignmentCard(a, jobs)).join("") || (archivedMarkup ? "" : empty("assignments", !catalog.assignments.length))) + archivedMarkup;
    // Nothing listed, though jobs exist: say which status holds them rather than "no match" (Clear filters returns to Current, so
    // without this an archive-only list would loop): superseded or cancelled ones under All statuses, else certified ones under Archived.
    const inScope = library.query || library.course ? [] : jobs.filter(b => !library.assignment || b.assignment?.id === library.assignment);
    const whose = library.assignment ? " for this assignment" : "";
    const elsewhere = library.status === "current" && inScope.some(b => !archivedJob(b) && endedJob(b))
      ? {title: `No current print jobs${whose}`, body: "Superseded and cancelled jobs are listed under All statuses.", label: "Show all statuses", status: "all"}
      : ["current", "all"].includes(library.status) && inScope.some(archivedJob)
      ? {title: library.status === "current" ? `No current print jobs${whose}` : `Every print job${whose} is archived`,
          body: "Print jobs of assessments whose grades are certified are under Archived (grades certified).", label: "Show archived jobs", status: "archived"} : null;
    const onlyElsewhere = elsewhere ? emptyMarkup({kind: "filtered", icon: "print", title: elsewhere.title, body: elsewhere.body, action: {label: elsewhere.label, data: {"show-status": elsewhere.status}}}) : "";
    const batchMarkup = groups.slice(jobsView ? offset : 0, (jobsView ? offset : 0) + PAGE_SIZE).map(group => groupMarkup(group, siblings)).join("") || onlyElsewhere || empty("batches", !catalog.batches.length);
    // Only replace changed results, preserving keyboard focus during polling.
    const focused = document.activeElement;
    const focusKey = ["data-assignment", "data-assignment-jobs", "data-batch"].find(key => focused?.hasAttribute(key));
    const focusValue = focusKey ? focused.getAttribute(focusKey) : null;
    const draftMarkup = jobsView || library.query ? "" : quizDrafts.cards(drafts.filter(d => !library.course || d.definition_id === library.course));
    for (const [id, markup] of [["#document-quiz-drafts", draftMarkup], ["#document-assignments", assignmentMarkup], ["#document-batches", batchMarkup]]) {
      const host = root.querySelector(id);
      if (renderedResults.get(host) !== markup) { host.innerHTML = markup; renderedResults.set(host, markup); }
    }
    if (focusKey && !focused.isConnected) [...root.querySelectorAll(`[${focusKey}]`)].find(button => button.getAttribute(focusKey) === focusValue)?.focus({preventScroll: true});
    root.querySelectorAll("[data-assignment]").forEach(button => { button.onclick = () => wizard.open({assignment: assignments.get(button.dataset.assignment)}); });
    root.querySelectorAll("[data-assignment-rename]").forEach(button => { button.onclick = () => renameDialog(assignments.get(button.dataset.assignmentRename)); });
    root.querySelectorAll("[data-assignment-makeup]").forEach(button => { button.onclick = () => void import("./makeup-drafts.js").then(({openMakeupDrafts}) =>
      openMakeupDrafts({api, escape, toast, showDialog, dialogHeader}, {planId: button.dataset.assignmentMakeup, title: button.dataset.title || ""})); });
    root.querySelectorAll("[data-assignment-given-on]").forEach(button => { button.onclick = () => void givenOnDialog(button, assignments.get(button.dataset.assignmentGivenOn), button.dataset.term); });
    root.querySelectorAll("[data-assignment-set-up-grading]").forEach(button => { button.onclick = () => void setUpGrading(button, assignments.get(button.dataset.assignmentSetUpGrading), button.dataset.term); });
    root.querySelectorAll("[data-assignment-canvas]").forEach(button => { button.onclick = () => {
      const a = assignments.get(button.dataset.assignmentCanvas);
      void canvasPlacement.open({termId: button.dataset.term, assignmentId: a.id, title: `${a.code} · ${a.title}`});
      // Where it stands is read again once the window closes.
      document.querySelector("#dialog")?.addEventListener("close", () => { canvasStatus.delete(button.dataset.term); void loadCanvasStatus(); }, {once: true});
    }; });
    root.querySelectorAll("[data-assignment-jobs]").forEach(button => { button.onclick = () => { showJobs(button.dataset.assignmentJobs); root.querySelector("#document-tab-batches").focus(); }; });
    root.querySelectorAll("[data-batch]").forEach(button => { button.onclick = () => detail(button.dataset.batch).catch(e => toast(e.message, true)); });
    root.querySelectorAll("[data-clear-filters]").forEach(button => { button.onclick = clearFilters; });
    root.querySelectorAll("[data-show-status]").forEach(button => { button.onclick = () => { library.status = button.dataset.showStatus; library.page = 1; root.querySelector("#document-status").value = library.status; drawLibrary(); }; });
    const attention = root.querySelector("[data-show-attention]");
    if (attention) attention.onclick = () => { Object.assign(library, {tab: "batches", status: "attention", assignment: "", page: 1}); root.querySelector("#document-status").value = "attention"; drawLibrary(); root.querySelector("#document-tab-batches").focus(); };
    root.querySelector(".document-pagination").hidden = pages < 2;
    root.querySelector("#document-page-number").textContent = `Page ${library.page} of ${pages}`;
    root.querySelector("#document-previous").disabled = library.page === 1;
    root.querySelector("#document-next").disabled = library.page === pages;
  }

  // Where an assignment stands in the term's Canvas copy (canvas/README.md "Putting a Faculty assignment on Canvas"), one rule for
  // its card and its assignment's Create area: the row Faculty's records hold for it, and the words shown beside Put/Update on Canvas.
  function canvasStanding(canvas, assignmentId) {
    const onCanvas = canvas?.configured ? (canvas.assignments || []).find(row => row.faculty_assignment === assignmentId) || null : null;
    const fact = !onCanvas ? "" : onCanvas.pushed && onCanvas.pushed === onCanvas.total ? "On Canvas"
      : onCanvas.pushed ? `On Canvas in ${onCanvas.pushed} of ${plural(onCanvas.total, "course")}` : "In the Canvas copy, not pushed yet";
    return {onCanvas, fact};
  }

  async function loadCanvasStatus() {
    const terms = [...new Set((catalog?.lifecycle || []).map(row => row.term_id))].filter(term => !(Date.now() - (canvasStatus.get(term)?.at || 0) < 30000));
    if (!terms.length) return;
    const read = await Promise.all(terms.map(term => api(`/api/canvas/content/${encodeURIComponent(term)}/assignments/status`).then(data => [term, data], () => [term, null])));
    for (const [term, data] of read) canvasStatus.set(term, {at: Date.now(), data});
    drawLibrary();
  }

  async function loadMakeupsOwed() {
    if (Date.now() - makeupsOwed.at < 30000) return;
    makeupsOwed.at = Date.now();
    const rows = await api("/api/absences/makeups").then(data => data.makeups || [], () => null);
    if (!rows) return;
    const counts = new Map();
    for (const row of rows) if (row.state === "owed") counts.set(row.event_id, (counts.get(row.event_id) || 0) + 1);
    const changed = counts.size !== makeupsOwed.counts.size || [...counts].some(([key, n]) => makeupsOwed.counts.get(key) !== n);
    makeupsOwed = {at: makeupsOwed.at, counts};
    if (changed) drawLibrary();
  }

  async function refreshBatches() {
    clearTimeout(refreshTimer);
    // Explicit print operations refresh the active assignment surface before opening
    // their result, so new stack siblings and superseded files use the current catalog.
    if (activeWorkspace()) { await workspaceSurface.onChanged(); return true; }
    const root = document.querySelector("#documents-section");
    if (!root.querySelector("#document-batches") || root.hidden) return false;
    const current = ++generation, requestedRoute = location.hash;
    const error = root.querySelector("#document-library-error");
    try {
      const [loaded, listed] = await Promise.all([api("/api/documents/catalog"), quizDrafts.load().catch(() => drafts)]);
      if (current !== generation || root.hidden || location.hash !== requestedRoute) return false;
      catalog = loaded;
      drafts = listed;
      error.hidden = true;
      drawLibrary();
      void loadCanvasStatus();
      void loadMakeupsOwed();
      if (catalog.batches.some(b => !b.superseded_at && working.has(b.status)) || drafts.some(d => ["drafting", "approving", "rebuilding"].includes(d.state))) refreshTimer = setTimeout(() => whenVisible(() => { void refreshBatches(); }), 3000);
      return true;
    } catch (cause) {
      if (current !== generation || root.hidden || location.hash !== requestedRoute) return false;
      error.querySelector("span").textContent = `${catalog ? "Couldn't refresh. Showing the last loaded results. " : "Couldn't load assignments. "}${cause.message}`;
      error.hidden = false;
      root.querySelector(".loading-state")?.remove();
      return false;
    }
  }

  async function exportEmail(batch) {
    const intended = opening();
    const settings = await api(`/api/documents/batches/${batch.id}/email`);
    if (!intended()) return;
    const tooLarge = settings.attachment_bytes > settings.max_attachment_bytes;
    const unavailable = !settings.configured || tooLarge;
    const message = !settings.configured ? "Connect Gmail on the Response Cards page to email print PDFs. You can still download the print PDF." : tooLarge ? "This PDF exceeds the email attachment limit (3.3 MiB). Use Download print PDF." : "";
    showDialog(`${dialogHeader("Export print job by email", `${escape(batch.title)} · ${escape(batch.version_label)}`)}<div class="dialog-body" id="document-email-export"><p class="section-lead"><strong>PDF attachment</strong> · ${batch.quantity * batch.page_count} pages · ${(settings.attachment_bytes / 1024 / 1024).toFixed(2)} MiB</p><p class="supporting-copy">${escape(batch.code)} · ${escape(batch.term)} ${batch.year} · ${escape(placeOf(batch))}${settings.sender ? ` · Sent from ${escape(settings.sender)}` : ""}</p><form id="document-email-form" class="detail-group"><label class="field">Send to<select name="destination" aria-label="Send to">${settings.approved_recipients.map(email => `<option value="${escape(email)}" ${email === settings.default_recipient ? "selected" : ""}>${escape(email)}${email === settings.default_recipient ? " (default)" : ""}</option>`).join("")}<option value="__custom__">Custom email address</option></select></label><label class="field" id="document-email-custom" hidden>Custom email address<input name="custom_email" aria-label="Custom email address" type="email" maxlength="254" autocomplete="email" disabled></label><p class="field-hint">The validated print PDF will be attached to an email sent to the address above.</p>${message ? `<p>${escape(message)}</p>` : ""}<p role="alert" class="form-error"></p><p id="document-email-result" role="status"></p><div class="inbox-job-actions"><button class="button primary" type="submit" ${unavailable ? "disabled" : ""}>Send email</button><button class="button" type="button" id="document-email-back">Back to print job</button></div></form><section aria-labelledby="document-email-history-title"><h3 id="document-email-history-title">Email exports</h3><div class="detail-group"><div id="document-email-history"></div><button class="text-button" id="document-email-refresh">Refresh email status</button></div></section></div>`);
    const host = document.querySelector("#document-email-export");
    const active = dialogSession(host, dialog());
    const form = host.querySelector("form");
    const button = form.querySelector('button[type="submit"]');
    const statuses = {pending:"Queued", sending:"Sending", accepted:"Accepted for delivery", failed:"Not sent", uncertain:"Delivery unconfirmed"};
    let requestId = crypto.randomUUID();
    let sent = false;
    let busy = false;
    let currentExport = null;
    let currentStatus = null;
    function updateButton() {
      button.disabled = busy || sent || unavailable;
      button.textContent = busy ? "Submitting…" : ({pending:"Email queued", sending:"Sending email…", accepted:"Email accepted", failed:"Email not sent", uncertain:"Delivery unconfirmed"}[currentStatus] || "Send email");
      button.setAttribute("aria-busy", String(busy || currentStatus === "sending"));
    }
    function showResult(item) {
      currentStatus = item.status;
      host.querySelector("#document-email-result").textContent = `${statuses[item.status]}: ${item.recipient}.${item.status === "accepted" ? " Check your inbox. You can close this window." : ""}`;
      updateButton();
    }
    function history(items) {
      host.querySelector("#document-email-history").innerHTML = items.map(item => `<p><strong>${escape(statuses[item.status] || item.status)}</strong> · ${escape(item.recipient)}<br><small>${escape(new Date(item.created_at).toLocaleString())}</small>${item.error ? `<br>${escape(item.error)}` : ""}</p>`).join("") || "<p>No email exports yet.</p>";
      const current = items.find(item => item.id === currentExport);
      if (current) showResult(current);
    }
    async function refresh() {
      if (!active()) return;
      const result = await api(`/api/documents/batches/${batch.id}/email`);
      if (!active()) return;
      history(result.exports);
      if (result.exports.some(item => ["pending","sending"].includes(item.status))) {
        setTimeout(() => refresh().catch(error => toast(error.message, true)), 2000);
      }
    }
    function changed() {
      sent = false; currentExport = null; currentStatus = null; requestId = crypto.randomUUID();
      const custom = form.elements.destination.value === "__custom__";
      host.querySelector("#document-email-custom").hidden = !custom;
      form.elements.custom_email.disabled = !custom;
      form.elements.custom_email.required = custom;
      updateButton();
      host.querySelector("#document-email-result").textContent = "";
    }
    form.elements.destination.onchange = changed;
    form.elements.custom_email.oninput = changed;
    form.onsubmit = async event => {
      event.preventDefault();
      if (busy || sent || unavailable) return;
      busy = true; updateButton();
      form.querySelector('[role="alert"]').textContent = "";
      const recipient = form.elements.destination.value === "__custom__" ? form.elements.custom_email.value.trim() : form.elements.destination.value;
      form.elements.destination.disabled = true;
      form.elements.custom_email.readOnly = true;
      try {
        const result = await api(`/api/documents/batches/${batch.id}/email`, "POST", {recipient, output_sha256:settings.output_sha256, request_id:requestId});
        sent = true;
        currentExport = result.id;
        showResult(result);
        await refresh();
      } catch (error) { form.querySelector('[role="alert"]').textContent = error.message; }
      finally { busy = false; updateButton(); form.elements.destination.disabled = false; form.elements.custom_email.readOnly = false; }
    };
    updateButton();
    history(settings.exports);
    host.querySelector("#document-email-refresh").onclick = () => refresh().catch(error => toast(error.message, true));
    host.querySelector("#document-email-back").onclick = () => detail(batch.id).catch(error => toast(error.message, true));
    if (settings.exports.some(item => ["pending","sending"].includes(item.status))) refresh().catch(error => toast(error.message, true));
  }

  // Print at Home (DOCUMENTS "SEND TO PRINTER"; requirement, 2026-10-18): the home printer prints the same verified file the Print button opens, a
  // job's PDF, a stack's interleaved PDF or a room's PDF. Only this click approves it; the worker never resends an unconfirmed send.
  const PRINTER_SIDES_KEY = "faculty:printer-sides";
  async function sendToPrinter({kind, batches, chartId = null, subtitle}, back) {
    const intended = opening();
    const query = `kind=${kind}&batches=${encodeURIComponent(batches.map(item => item.id).join(","))}${chartId ? `&chart=${encodeURIComponent(chartId)}` : ""}`;
    const settings = await api(`/api/documents/printer?${query}`);
    if (!intended()) return;
    const printer = settings.printer;
    const reasons = {"media-empty": "out of paper", "media-jam": "paper jam", "toner-empty": "out of toner", "toner-low": "toner low", "door-open": "a door is open", "cover-open": "a cover is open", "media-needed": "needs paper"};
    const problems = (printer?.reasons || []).map(reason => reasons[reason.replace(/-(report|warning|error)$/, "")] || reason.replace(/-(report|warning|error)$/, "").replace(/-/g, " "));
    const unavailable = !settings.configured || !printer?.reachable || printer.accepting === false;
    const statusText = !settings.configured ? "No printer is set up yet. Open the print PDF and print it yourself."
      : !printer.reachable ? "The printer is not answering. Check that it is on and connected, then reopen this window."
      : `${printer.name || "Printer"} · ${{idle: "Ready", processing: "Printing", stopped: "Stopped"}[printer.state] || printer.state}${problems.length ? ` · ${problems.join(", ")}` : ""}`;
    let remembered = "two-sided-long-edge";
    try { remembered = localStorage.getItem(PRINTER_SIDES_KEY) || remembered; } catch { /* a convenience only */ }
    const sides = settings.two_sided_allowed && remembered !== "one-sided" ? "two-sided-long-edge" : "one-sided";
    const before = settings.sends.filter(item => ["accepted", "sending", "pending", "uncertain"].includes(item.status));
    const statuses = {pending: "Queued", sending: "Sending", accepted: "Printer accepted the job", failed: "Not printed", uncertain: "Unconfirmed"};
    showDialog(`${dialogHeader("Print at Home", escape(subtitle))}<div class="dialog-body" id="document-printer-send">
      <p class="section-lead"><strong>${plural(settings.pages, "page")}</strong>${settings.copies ? ` · ${plural(settings.copies, "copy", "copies")}` : ""}</p>
      <p class="supporting-copy" data-printer-status>${escape(statusText)}</p>
      <form class="detail-group" id="document-printer-form">
        <label class="field">Sides<select name="sides" aria-label="Sides">
          <option value="two-sided-long-edge" ${sides !== "one-sided" ? "selected" : ""} ${settings.two_sided_allowed ? "" : "disabled"}>Two-sided</option>
          <option value="one-sided" ${sides === "one-sided" ? "selected" : ""}>One-sided</option></select></label>
        ${settings.two_sided_allowed ? "" : '<p class="field-hint">One-sided only: a copy with an odd number of pages would share a sheet with the next copy.</p>'}
        <p class="field-hint">Printed once, in black and white, at actual size.</p>
        ${before.length ? `<p class="callout" data-tone="warning" data-printer-before>Already sent to the printer ${escape(new Date(before[0].created_at).toLocaleString())}. Sending again prints the same stamps twice.</p>` : ""}
        <p role="alert" class="form-error"></p><p id="document-printer-result" role="status"></p>
        <div class="inbox-job-actions"><button class="button primary" type="submit" ${unavailable ? "disabled" : ""}>Print at Home</button><button class="button" type="button" id="document-printer-back">Back to print job</button></div>
      </form>
      <section aria-labelledby="document-printer-history-title"><h3 id="document-printer-history-title">Printed at Home</h3><div class="detail-group"><div id="document-printer-history"></div><button class="text-button" id="document-printer-refresh">Refresh printer status</button></div></section>
    </div>`);
    const host = document.querySelector("#document-printer-send");
    const active = dialogSession(host, dialog());
    const form = host.querySelector("form");
    const button = form.querySelector('button[type="submit"]');
    let requestId = crypto.randomUUID(), busy = false, current = null, status = null;
    const updateButton = () => {
      button.disabled = busy || Boolean(current) || unavailable;
      button.textContent = busy ? "Sending…" : ({pending: "Queued for the printer", sending: "Sending to the printer…", accepted: "Printer accepted the job", failed: "Not printed", uncertain: "Unconfirmed"}[status] || "Print at Home");
      button.setAttribute("aria-busy", String(busy || status === "sending"));
    };
    const history = items => {
      host.querySelector("#document-printer-history").innerHTML = items.map(item => `<p><strong>${escape(statuses[item.status] || item.status)}</strong> · ${escape(plural(item.page_count, "page"))} · ${item.sides === "one-sided" ? "one-sided" : "two-sided"}<br><small>${escape(new Date(item.created_at).toLocaleString())}</small>${item.error ? `<br>${escape(item.error)}` : ""}</p>`).join("") || "<p>Nothing printed at home yet.</p>";
      const mine = items.find(item => item.id === current);
      if (mine) {
        status = mine.status;
        host.querySelector("#document-printer-result").textContent = `${statuses[mine.status]}.${mine.status === "accepted" ? " Collect the pages at the printer. You can close this window." : ""}`;
        updateButton();
      }
    };
    const refresh = async () => {
      if (!active()) return;
      const result = await api(`/api/documents/printer?${query}`);
      if (!active()) return;
      history(result.sends);
      if (result.sends.some(item => ["pending", "sending"].includes(item.status))) setTimeout(() => refresh().catch(error => toast(error.message, true)), 2000);
    };
    form.elements.sides.onchange = () => {
      try { localStorage.setItem(PRINTER_SIDES_KEY, form.elements.sides.value); } catch { /* a convenience only */ }
      if (!current) requestId = crypto.randomUUID();
    };
    form.onsubmit = async event => {
      event.preventDefault();
      if (busy || current || unavailable) return;
      busy = true; updateButton();
      form.querySelector('[role="alert"]').textContent = "";
      form.elements.sides.disabled = true;
      try {
        const result = await api("/api/documents/printer", "POST", {kind, batch_ids: settings.outputs.map(item => item.id), outputs: settings.outputs,
          seating_chart_id: chartId, sides: form.elements.sides.value, request_id: requestId});
        current = result.id;
        history([result, ...settings.sends]);
        await refresh();
      } catch (error) { form.querySelector('[role="alert"]').textContent = error.message; form.elements.sides.disabled = false; }
      finally { busy = false; updateButton(); }
    };
    host.querySelector("#document-printer-refresh").onclick = () => refresh().catch(error => toast(error.message, true));
    host.querySelector("#document-printer-back").onclick = back;
    updateButton();
    history(settings.sends);
    if (settings.sends.some(item => ["pending", "sending"].includes(item.status))) refresh().catch(error => toast(error.message, true));
  }
  const printerButton = kind => `<button class="button" type="button" data-printer-send="${kind}">Print at Home</button>`;

  async function roomExportLinks(b, root) {
    // This job is one section of a room; the chart's export group (same request prefix) gives the whole-room files.
    const chart = await api(`/api/seating/${encodeURIComponent(b.seating_chart_id)}`);
    const mine = chart.print_batches.find(item => item.id === b.id);
    const holder = root.querySelector("#document-room-export");
    if (!mine || !holder || !holder.isConnected) return;
    const siblings = chart.print_batches.filter(item => item.export === mine.export).sort((x, y) => x.section_number - y.section_number)
      .map(item => ({...item, stamps_only: isStampsOnly({label: item.version_label})}));
    const sections = siblings.map(item => item.section_number).join(", ");
    holder.innerHTML = `<details class="disclosure document-batch-disclosure" data-batch-room>
      <summary><span>Whole-room files · ${escape(chart.room_name)}</span><span class="disclosure-hint">${siblings.length} section${siblings.length === 1 ? "" : "s"}</span></summary>
      <div class="disclosure-body document-batch-disclosure-body">
        <p class="supporting-copy">Section${siblings.length === 1 ? "" : "s"} ${escape(sections)} in seat order, rows interleaved across sections. These files carry the same stamps as the section files, so print one or the other.</p>
        ${exportFiles(chart.id, siblings, {primary: false, extra: siblings.every(item => item.stamps_only) ? "" : printerButton("room")})}
      </div>
    </details>`;
    holder.hidden = false;
    holder.querySelector("[data-printer-send]")?.addEventListener("click", () => sendToPrinter({kind: "room", batches: siblings, chartId: chart.id,
      subtitle: `${b.title} · Whole room · ${chart.room_name}`}, () => detail(b.id).catch(error => toast(error.message, true))).catch(error => toast(error.message, true)));
  }

  function auditMarkup(events) {
    const at = iso => new Date(iso);
    const dateText = iso => at(iso).toLocaleDateString(undefined, {month: "short", day: "numeric"});
    const timeText = iso => at(iso).toLocaleTimeString(undefined, {hour: "numeric", minute: "2-digit"});
    const range = (first, last) => first === last || dateText(first) !== dateText(last) && timeText(first) === timeText(last) ? `${dateText(first)} ${timeText(first)}`
      : dateText(first) === dateText(last) ? `${dateText(first)} ${timeText(first)}–${timeText(last)}` : `${dateText(first)} ${timeText(first)} – ${dateText(last)} ${timeText(last)}`;
    const kinds = new Map();
    events.forEach(item => {
      const entry = kinds.get(item.kind) || {kind: item.kind, count: 0, first: item.created_at, last: item.created_at};
      entry.count += 1; entry.last = item.created_at;
      kinds.set(item.kind, entry);
    });
    const summary = [...kinds.values()].map(entry => `<li><strong>${escape(eventName(entry.kind))}${entry.count > 1 ? ` ×${entry.count.toLocaleString()}` : ""}</strong><span>${escape(range(entry.first, entry.last))}</span></li>`).join("");
    const all = events.map(item => `<li><strong>${escape(eventName(item.kind))}</strong><span>${escape(item.actor)} · ${escape(at(item.created_at).toLocaleString())}</span></li>`).join("");
    return `<ul class="document-batch-records document-batch-events" data-audit-summary>${summary}</ul>
      <details class="disclosure document-batch-all-events" data-audit-all><summary><span>All ${events.length.toLocaleString()} events</span></summary><div class="disclosure-body"><ul class="document-batch-records">${all}</ul></div></details>`;
  }

  // The other versions' jobs of the stack a term job was printed in (one request, one assignment, several versions), itself
  // included and sorted by version; empty for any other job. The Print page lists them as one group (groupKey).
  function stackOf(b) {
    const listed = catalog?.batches.find(item => item.id === b.id);
    if (!listed || listed.section_id || listed.origin !== "section") return [];
    const versionOf = item => catalog.versions.find(v => v.id === item.assignment_version_id);
    const all = catalog.batches.filter(item => !item.section_id && item.origin === "section" && item.export === listed.export && versionOf(item)?.assignment_id === b.assignment_id)
      .map(item => ({...item, version: versionOf(item)})).sort((x, y) => String(x.version?.label ?? "").localeCompare(String(y.version?.label ?? "")));
    return new Set(all.map(item => item.assignment_version_id)).size > 1 ? all : [];
  }

  async function detail(id) {
    const intended = opening();
    const b = await api(`/api/documents/batches/${id}`);
    if (!intended()) return;
    const ready = b.status === "ready";
    const canDownload = downloadable(b);
    const state = b.superseded_at ? "superseded" : b.status;
    const registration = b.origin === "registration";
    const stampsOnly = isStampsOnly({label: b.version_label});
    const job = {...b, stamps_only: stampsOnly};
    // This job as just read, not as the catalog last listed it (a supersede from this window is not in the catalog yet).
    const stack = registration ? [] : stackOf(b).map(item => item.id === b.id ? {...item, status: b.status, superseded_at: b.superseded_at} : item);
    // A stack is printed whole only while every version's job is current (stackFiles); otherwise each job says what it still prints.
    const stackJobEnded = item => Boolean(item.superseded_at) || item.status === "cancelled";
    const thisEnded = stackJobEnded(b);
    const stackEnded = stack.some(stackJobEnded);
    const names = new Map();
    if (b.copies.some(copy => copy.enrollment_id)) {
      try { (await (b.section_id ? sectionStudents(b.section_id) : termStudents(b.term_id))).students?.forEach(student => names.set(student.enrollment_id, student.name)); }
      catch { /* the copy list falls back to enrollment IDs */ }
    }
    if (!intended()) return;
    const cornerSize = (() => { try { return {"faculty-corners-10mm-v1": 10, "faculty-corners-7mm-v1": 7}[JSON.parse(b.configuration_json).id] || 0; } catch { return 0; } })();
    const corner = Boolean(cornerSize);
    const manifest = canDownload ? `<a class="text-button document-batch-manifest" ${viewLink(`/api/documents/batches/${encodeURIComponent(id)}/manifest`, "Manifest", escape)}>Manifest (JSON)</a>` : "";
    const exports = canDownload ? `<div class="document-batch-exports" role="group" aria-label="Export print PDF"><button class="button" data-operation="email-export">Export by email</button><button class="button" data-operation="drive-export">Export to external drive</button></div>` : "";
    const settingsLine = '<p class="document-batch-print-settings">Actual size / 100% <span>Printer copies: <strong>1</strong></span></p>';
    const cornerNote = corner ? `<p class="document-layout-description">Two ${cornerSize} mm Data Matrix marks per page, at top-right and bottom-left. Both identify the same page.</p>` : "";
    const ownFiles = () => canDownload ? `${jobFiles(job, {extra: stampsOnly ? "" : printerButton("job")})}${exports}${manifest}` : "";
    const printSection = registration
      ? `<section class="document-batch-print document-batch-registration" aria-labelledby="document-batch-print-title">
          <h3 id="document-batch-print-title"><span class="chip" data-tone="neutral">Not for printing</span></h3>
          <p>These labels were issued when scanned papers printed without labels were matched to students, so the papers could be graded. Nothing here needs printing.</p>
          ${canDownload ? `<details class="disclosure" data-registration-downloads><summary><span>Downloads</span></summary><div class="disclosure-body">${jobFiles(job, {primary: false})}${exports}${manifest}</div></details>` : ""}
        </section>`
      : stack.length && !stackEnded
      // A version of a stack printed in several versions: the stack is what is printed (its versions alternate copy by copy), so it
      // comes first; this version's own file, its exports and manifest sit in one closed disclosure that says they are this version's only.
      ? `<section class="document-batch-print" aria-labelledby="document-batch-print-title">
          <h3 id="document-batch-print-title">Print</h3>
          ${settingsLine}
          <p class="supporting-copy" data-stack-note>Version ${escape(b.version_label)} of ${stack.length} printed as one stack. Print the stack, once: this version’s own file carries the same stamps, so printing both, or either twice, duplicates the printed page identities.</p>
          ${stackFiles(stack, {extra: printerButton("stack")})}
          ${canDownload ? `<details class="disclosure document-batch-disclosure" data-version-only><summary><span>Version ${escape(b.version_label)} only</span><span class="disclosure-hint">${plural(b.quantity, "copy", "copies")}</span></summary><div class="disclosure-body document-batch-disclosure-body">${jobFiles(job, {primary: false})}${exports}<p class="field-hint">Exports send this version’s PDF only.</p>${manifest}</div></details>` : ""}
          ${cornerNote}
        </section>`
      : stack.length
      // The stack cannot be printed whole any more (a version's job was superseded or cancelled): this job prints from its own
      // file, or, ended itself, prints nothing; the note says which, never pointing at a job with no files.
      ? `<section class="document-batch-print" aria-labelledby="document-batch-print-title">
          <h3 id="document-batch-print-title">${thisEnded ? "Printed as one stack" : "Print this job"}</h3>
          ${thisEnded ? "" : settingsLine}
          <p class="supporting-copy" data-stack-note>Version ${escape(b.version_label)} of ${stack.length} printed as one stack. ${thisEnded
            ? `This job was ${b.superseded_at ? "superseded" : "cancelled"} and prints nothing more${stack.every(stackJobEnded) ? ", nor does the stack" : "; the other versions still print from their own jobs"}.`
            : `The stack file is no longer available (another version’s job was superseded or cancelled), so print this version from its own file, once; reusing it duplicates the printed page identities.`}</p>
          ${ownFiles()}
          ${cornerNote}
        </section>`
      : `<section class="document-batch-print" aria-labelledby="document-batch-print-title">
          <h3 id="document-batch-print-title">Print this job</h3>
          ${settingsLine}
          <p class="supporting-copy">The ${stampsOnly ? "labels contain" : "PDF contains"} ${plural(b.quantity, "copy", "copies")}. Reusing ${stampsOnly ? "them" : "it"} duplicates the printed page identities.</p>
          ${ownFiles()}
          ${cornerNote}
        </section>`;
    const facts = [`${b.code} · ${termText(b)} · ${placeOf(b)}`, b.label_mode === "first_page" ? "Labeled on the first page only" : "Labeled on every page"];
    // Came back in scans (task 13): copies seen in a scan still in the Inbox, who is not seen yet, and the scans themselves.
    const scanned = b.scanned || {copies: 0, total: b.copies.length, unseen: [], scans: []};
    const printed = !registration && (ready || b.superseded_at);
    // One copy: the student (or Unassigned) first, then the copy number and the seat caption without the name it repeats.
    const copyMarkup = c => {
      const student = c.enrollment_id ? names.get(c.enrollment_id) || c.enrollment_id : "Unassigned";
      const caption = c.caption ? c.caption.split(" · ").filter(part => part !== student).join(" · ") : "";
      return `<li><div><strong class="document-batch-student">${escape(student)}</strong><span>Copy ${c.copy_number}${caption ? ` · ${escape(caption)}` : ""}</span></div><button class="text-button" data-copy="${escape(c.id)}">Change assignment</button></li>`;
    };
    const unseenName = copy => copy.enrollment_id ? names.get(copy.enrollment_id) || "Linked student" : copy.caption || "Unassigned copy";
    const scanMarkup = !printed ? "" : `<section class="document-batch-scanned" data-batch-scanned aria-label="Came back in scans">
        <p><strong>${scanned.copies ? `Scanned: ${scanned.copies.toLocaleString()} of ${plural(scanned.total, "copy", "copies")}` : "No copy has come back in a scan yet"}</strong>${scanned.scans.length ? ` · ${scanned.scans.map((scan, index) => `<a href="#inbox/job/${encodeURIComponent(scan.job_id)}" data-leave>${scanned.scans.length === 1 ? "Open the scan" : `Scan ${index + 1}`}</a>`).join(", ")}` : ""}</p>
        ${scanned.copies && scanned.unseen.length ? `<details class="disclosure" data-unseen><summary><span>Not seen in any scan yet (${scanned.unseen.length.toLocaleString()})</span></summary><div class="disclosure-body"><ul class="document-batch-records">${scanned.unseen.map(copy => `<li><strong>${escape(unseenName(copy))}</strong><span>Copy ${copy.copy_number}${copy.caption && copy.enrollment_id ? ` · ${escape(copy.caption)}` : ""}</span></li>`).join("")}</ul></div></details>` : ""}
      </section>`;
    const menu = [
      !ready && b.status !== "cancelled" ? {label: "Cancel job", data: {operation: "cancel"}} : null,
      b.seating_chart_id && ["pending", "failed"].includes(b.status) ? {label: b.seat_marks ? "Remove seat numbers beside stamps" : "Add seat numbers beside stamps", data: {operation: "seat-marks"}} : null,
      canDownload ? {label: "Supersede print job…", data: {operation: "supersede"}, danger: true} : null,
    ].filter(Boolean);
    showDialog(`${dialogHeader(`${escape(b.title)} · ${escape(b.version_label)}`, `${plural(b.quantity, "copy", "copies")} · ${plural(b.page_count, "page")} per copy`)}
      <div class="dialog-body" id="document-batch-detail">
        <div class="document-batch-context">
          <div>${registration && ready && !b.superseded_at ? '<span class="chip document-batch-status" data-tone="neutral" data-status="ready">Issued for scanned papers</span>' : `<span class="chip document-batch-status" data-tone="${statusTones[state] || "neutral"}" data-status="${escape(state)}">${escape(statusNames[state] || state)}</span>`}
            ${facts.map(fact => `<p>${escape(fact)}</p>`).join("")}
            ${b.seating_chart_id ? `<p class="document-batch-seating">Seated${b.room_name ? ` in ${escape(b.room_name)}` : ""} · <a href="#seating/${encodeURIComponent(b.seating_chart_id)}" data-leave>Open chart</a></p>` : ""}</div>
          <button class="text-button" data-operation="refresh">Refresh status</button>
        </div>
        ${b.error ? `<p class="callout document-batch-error" data-tone="danger" role="alert">${escape(b.error)}</p>` : ""}
        ${printSection}
        ${scanMarkup}
        ${b.seating_chart_id ? `<p class="supporting-copy document-batch-seat-note">Each stamp shows the seat and name${b.seat_marks ? ", with the seat number printed in bold beside the square" : ""}. Labels carry the same stamps for papers printed elsewhere.</p>` : ""}
        ${b.seating_chart_id ? '<div id="document-room-export" hidden></div>' : ""}
        <details class="disclosure document-batch-disclosure" data-copy-assignments><summary id="document-copy-assignments-title"><span>Copy assignments</span><span class="disclosure-hint">${b.copies.length}</span></summary>
          <div class="disclosure-body document-batch-disclosure-body"><p class="supporting-copy">Administrative links to students; they do not establish authorship.</p>
            <ul class="document-batch-records document-batch-copies">${b.copies.map(copyMarkup).join("")}</ul>
          </div>
        </details>
        <details class="disclosure document-batch-disclosure" data-batch-audit><summary><span>Audit history</span><span class="disclosure-hint">${b.events.length}</span></summary>
          <div class="disclosure-body document-batch-disclosure-body">${auditMarkup(b.events)}</div>
        </details>
        <div class="inbox-job-actions document-batch-controls" role="group" aria-label="Manage print job">
          ${b.status === "failed" ? '<button class="button" data-operation="retry">Retry same job</button>' : ""}
          ${registration ? "" : '<button class="button" data-operation="reprint">Print more copies…</button>'}
          ${menu.length ? actionMenuMarkup({id: `job-actions-${id}`, label: "Job actions", items: menu}) : ""}
        </div>
      </div>`);
    const root = document.querySelector("#document-batch-detail");
    const alive = dialogSession(root, dialog());
    if (b.seating_chart_id) roomExportLinks(b, root).catch(() => {});
    root.querySelectorAll("[data-printer-send]").forEach(button => { button.onclick = () => {
      const stacked = button.dataset.printerSend === "stack";
      sendToPrinter({kind: stacked ? "stack" : "job", batches: stacked ? stack : [b],
        subtitle: `${b.title} · ${stacked ? "Every version interleaved" : b.version_label} · ${placeOf(b)}`},
        () => detail(b.id).catch(error => toast(error.message, true))).catch(error => toast(error.message, true));
    }; });
    root.querySelectorAll("[data-operation]").forEach(button => { button.onclick = async () => {
      setBusy(button, true);
      try {
        const operation = button.dataset.operation;
        if (operation === "email-export") await exportEmail(b);
        else if (operation === "drive-export") await exportToDrive(b, () => detail(b.id).catch(error => toast(error.message, true)));
        else if (operation === "reprint") { printMore(b); return; }
        else if (operation === "supersede") { confirmSupersede(b); return; }
        else if (operation === "seat-marks") {
          await api(`/api/documents/batches/${id}/seat-marks`, "POST", {seat_marks: !b.seat_marks});
          toast(b.seat_marks ? "Seat numbers will not be printed beside the stamps." : "Seat numbers will be printed beside the stamps when the job renders.");
          if (alive()) await detail(id);
        } else if (operation === "refresh") await detail(id);
        else { await api(`/api/documents/batches/${id}/${operation}`, "POST", {}); if (alive()) await detail(id); }
        await refreshBatches();
      } catch (cause) { toast(cause.message, true); setBusy(button, false); }
    }; });
    root.querySelectorAll("a[data-leave]").forEach(link => link.addEventListener("click", () => document.querySelector("#dialog").close()));
    root.querySelectorAll("[data-copy]").forEach(button => { button.onclick = () => assignCopy(b, button.dataset.copy).catch(cause => toast(cause.message, true)); });
    // While the worker prepares the job, look again every 3 s and redraw only when its state changes.
    if (working.has(b.status) && !b.superseded_at) {
      const look = async () => {
        if (!alive()) return;
        try {
          const next = await api(`/api/documents/batches/${id}`);
          if (!alive()) return;
          if (next.status !== b.status || next.superseded_at !== b.superseded_at || next.error !== b.error) {
            await detail(id);
            refreshBatches().catch(() => {});
            return;
          }
        } catch { /* the Refresh status button stays available */ }
        setTimeout(look, 3000);
      };
      setTimeout(look, 3000);
    }
  }

  // Print more copies: the same copies again, or extra unassigned copies. Either way a NEW job with new stamps is issued; the
  // earlier job and its stamps stay valid. The request ID is kept while the choice stays the same, so a repeated click
  // returns the same job instead of issuing a second one.
  function printMore(b) {
    const seated = Boolean(b.seating_chart_id);
    const named = b.copies.filter(copy => copy.enrollment_id).length;
    // A version of a stack printed in several versions: more of this version alone, or more of every version as one new stack (only
    // while every version's job is current: a superseded stack's versions were replaced).
    const all = stackOf(b);
    const stack = all.every(item => !item.superseded_at && item.status !== "cancelled") ? all : [];
    showDialog(`<form id="document-reprint" novalidate>${dialogHeader("Print more copies", `${escape(b.title)} · ${escape(b.version_label)} · ${escape(placeOf(b))}`)}
      <div class="dialog-body">
        <p class="section-lead">${stack.length ? `Every option issues a new print job with new stamps for Version ${escape(b.version_label)} only, unless you choose every version. This job and its stamps stay valid.`
          : "Every option issues a new print job with new stamps. This job and its stamps stay valid."}</p>
        <fieldset class="document-reprint-choices"><legend>What to print</legend>
          <label class="checkbox-field"><input type="radio" name="kind" value="same" checked>The same ${plural(b.quantity, "copy", "copies")} again${seated ? ", in seat order for the students seated now" : named ? `, linked to the same ${plural(named, "student")}` : ""}</label>
          <label class="checkbox-field"><input type="radio" name="kind" value="extra">Extra unassigned copies</label>
          ${stack.length ? `<label class="checkbox-field"><input type="radio" name="kind" value="stack">Extra unassigned copies of every version (one stack)</label>` : ""}
          <label class="field document-reprint-quantity">${stack.length ? "Number of extra copies (of each version)" : "Number of extra copies"}<input type="number" name="quantity" min="1" max="500" value="5" disabled></label>
        </fieldset>
        <p class="form-error" role="alert" hidden></p>
      </div>
      <footer class="dialog-footer"><button type="button" class="button" data-back>Back to print job</button><button type="submit" class="button primary">Create print job</button></footer></form>`);
    const form = document.querySelector("#document-reprint");
    const current = dialogSession(form, dialog());
    const error = form.querySelector(".form-error");
    let requestId = crypto.randomUUID();
    form.addEventListener("change", () => {
      requestId = crypto.randomUUID();
      form.elements.quantity.disabled = !["extra", "stack"].includes(form.elements.kind.value);
      error.hidden = true;
    });
    form.elements.quantity.addEventListener("input", () => { requestId = crypto.randomUUID(); });
    form.querySelector("[data-back]").onclick = () => detail(b.id).catch(cause => toast(cause.message, true));
    form.onsubmit = async event => {
      event.preventDefault();
      const submit = form.querySelector('button[type="submit"]');
      if (submit.disabled || !current()) return;
      const request = requestId;
      const kind = form.elements.kind.value;
      const extra = kind === "extra";
      const quantity = Number(form.elements.quantity.value);
      if ((extra || kind === "stack") && !(Number.isInteger(quantity) && quantity >= 1 && quantity <= 500)) { error.textContent = "Enter between 1 and 500 extra copies."; error.hidden = false; return; }
      setBusy(submit, true);
      // A job printed for the whole term prints more for the same term and sections, naming no section.
      const owner = b.section_id ? {section_id: b.section_id} : {section_id: null, term_id: b.term_id, section_ids: (b.sections || []).map(s => s.id)};
      try {
        if (kind === "stack") {
          // One term job per version, one request (each version's ID under it, so a repeated click returns the same jobs): they share
          // an export, so the Print page groups them and interleaves them like the approval's stack.
          const made = [];
          for (const item of stack) {
            if (!current()) return;
            made.push(await api("/api/documents/batches", "POST", {version_id: item.assignment_version_id, section_id: null, term_id: b.term_id,
              section_ids: (b.sections || []).map(s => s.id), quantity, students: null, request_id: `${request}:${item.assignment_version_id}`}));
          }
          toast(`New stack queued: ${plural(quantity, "copy", "copies")} of each of ${plural(stack.length, "version")}, with new stamps.`);
          // The catalog first, so the new job's window finds its stack (stackOf reads the catalog).
          await refreshBatches().catch(() => {});
          if (current()) await detail(made[0].id);
          return;
        }
        const result = extra
          ? await api("/api/documents/batches", "POST", {version_id: b.assignment_version_id, ...owner, quantity, students: null, request_id: request})
          : seated
            ? await api("/api/documents/batches", "POST", {version_id: b.assignment_version_id, section_id: b.section_id, seating_chart_id: b.seating_chart_id, extras: b.copies.filter(c => !c.caption).length, seat_marks: Boolean(b.seat_marks), request_id: requestId, reprint_of_batch_id: b.id})
            : await api("/api/documents/batches", "POST", {version_id: b.assignment_version_id, ...owner, quantity: b.quantity, students: b.copies.map(c => c.enrollment_id), request_id: requestId, reprint_of_batch_id: b.id});
        toast("New print job queued with new stamps.");
        if (current()) await detail(result.id);
        refreshBatches().catch(() => {});
      } catch (cause) {
        if (form.isConnected) { error.textContent = cause.message; error.hidden = false; setBusy(submit, false); }
        else toast(cause.message, true);
      }
    };
  }

  // Supersede cannot be undone: every stamp of the job is marked superseded and scans of this paper become non-current.
  function confirmSupersede(b) {
    const stamps = b.quantity * b.page_count;
    const stack = stackOf(b);
    showDialog(`<form id="document-supersede" novalidate>${dialogHeader("Supersede this print job?", `${escape(b.title)} · ${escape(b.version_label)} · ${escape(placeOf(b))}`)}
      <div class="dialog-body">
        <p>All ${plural(b.quantity, "copy", "copies")} of this job (${plural(stamps, "page stamp")}) will be marked superseded. Scans of this paper will then count as non-current paper and need your review, and nothing prints from this job again.</p>
        ${stack.length ? `<p class="callout" data-tone="warning" data-stack-warning>Version ${escape(b.version_label)} is one of ${stack.length} versions printed as one stack. Superseding it ends the interleaved stack file; the other versions stay printable only one by one.</p>` : ""}
        <p><strong>This cannot be undone.</strong> The job and its stamps stay on record; stamps are never reused.</p>
        <label class="checkbox-field"><input type="checkbox" name="understood">I understand that scans of these ${plural(b.quantity, "copy", "copies")} will count as superseded</label>
        <p class="form-error" role="alert" hidden></p>
      </div>
      <footer class="dialog-footer"><button type="button" class="button" data-back>Back to print job</button><button type="submit" class="button danger" disabled>Supersede print job</button></footer></form>`);
    const form = document.querySelector("#document-supersede");
    const current = dialogSession(form, dialog());
    const submit = form.querySelector('button[type="submit"]');
    form.elements.understood.onchange = () => { submit.disabled = !form.elements.understood.checked; };
    form.querySelector("[data-back]").onclick = () => detail(b.id).catch(cause => toast(cause.message, true));
    form.onsubmit = async event => {
      event.preventDefault();
      if (submit.disabled || !form.elements.understood.checked || !current()) return;
      setBusy(submit, true);
      try {
        await api(`/api/documents/batches/${b.id}/supersede`, "POST", {});
        toast("Print job superseded.");
        if (current()) await detail(b.id);
        refreshBatches().catch(() => {});
      } catch (cause) {
        const error = form.querySelector(".form-error");
        if (form.isConnected) { error.textContent = cause.message; error.hidden = false; setBusy(submit, false); }
        else toast(cause.message, true);
      }
    };
  }

  // Change which student a printed copy is linked to, chosen by name from the section's roster (for a job printed for the
  // whole term, from the rosters of the sections it lists, grouped by section).
  async function assignCopy(b, copyId) {
    const intended = opening();
    const copy = b.copies.find(item => item.id === copyId);
    const roster = await (b.section_id ? sectionStudents(b.section_id) : termStudents(b.term_id));
    if (!intended()) return;
    const option = item => ({value: item.enrollment_id, text: item.name});
    const groups = !roster.students?.length ? [{label: "Roster", options: (roster.enrollments || []).map(enrollment => ({value: enrollment, text: enrollment}))}]
      : b.section_id ? [{label: `Section ${b.section_number} roster`, options: roster.students.map(option)}]
      : (b.sections || []).map(section => ({label: `Section ${section.number}`, options: roster.students.filter(item => item.section_id === section.id).map(option)})).filter(group => group.options.length);
    const picker = studentPicker(escape, {id: "document-copy-student", name: "enrollment_id", label: "Student", value: copy?.enrollment_id || "",
      groups: [{label: "", options: [{value: "", text: "Unassigned"}]}, ...groups]});
    showDialog(`${dialogHeader("Assign printed copy", `Copy ${copy?.copy_number ?? ""} · This administrative link does not establish authorship.`)}<form class="dialog-body" id="document-copy-assignment"><div class="field">Student${picker.markup}</div><label class="field">Reason<input name="reason" required maxlength="1000"></label><p role="alert" class="form-error"></p><div class="inbox-job-actions"><button type="submit" class="button primary">Save assignment</button><button type="button" class="button" data-back>Back to print job</button></div></form>`);
    const form = document.querySelector("#document-copy-assignment");
    const current = dialogSession(form, dialog());
    picker.bind(form);
    form.querySelector("[data-back]").onclick = () => detail(b.id).catch(cause => toast(cause.message, true));
    form.onsubmit = event => { event.preventDefault(); perform(form, async () => {
      const data = new FormData(form);
      await api(`/api/documents/copies/${copyId}/assign`, "POST", {enrollment_id: data.get("enrollment_id") || null, reason: data.get("reason")});
      if (current()) await detail(b.id);
    }); };
  }
  // A paper/print link whose read failed says so above what it showed before (never a blank page), with Try again.
  function workspaceFailed(root, error) {
    if (!root.querySelector(":scope > .assignment-block")) root.replaceChildren();
    let box = root.querySelector(":scope > [data-workspace-error]");
    if (!box) { box = document.createElement("div"); box.dataset.workspaceError = ""; root.prepend(box); }
    box.innerHTML = `${calloutMarkup({tone: "danger", title: "This assignment could not be read", body: error.message})}<button type="button" class="text-button" data-workspace-retry>Try again</button>`;
    box.querySelector("[data-workspace-retry]").onclick = () => void render(true).catch(cause => toast(cause.message, true));
  }

  async function renderWorkspace(host, context, active = () => true, onChanged = () => render(true)) {
    const [data, canvas] = await Promise.all([api('/api/documents/catalog'),
      api(`/api/canvas/content/${encodeURIComponent(context.term_id)}/assignments/status`).catch(() => null)]);
    if (!active()) return;
    workspaceSurface = {host, active, onChanged};
    catalog = data;
    const assignment = data.assignments.find(a => a.id === context.assignment_id);
    if (!assignment) throw new Error('Assignment is no longer available.');
    const versions = data.versions.filter(v => v.assignment_id === assignment.id);
    const life = data.lifecycle.find(item => item.assignment_id === assignment.id && item.term_id === context.term_id);
    const jobs = libraryJobs().filter(b => b.assignment?.id === assignment.id && b.term_id === context.term_id);
    const siblings = new Map(groupsOf(jobs).map(group => [group.key, group.items]));
    const jobGroups = list => groupsOf(list).map(group => groupMarkup(group, siblings)).join('');
    // CANVAS: Put on Canvas keeps where it stands beside it, as on the assignment card (canvas/README.md).
    const {onCanvas, fact: canvasFact} = canvasStanding(canvas, assignment.id);
    const name = `${assignment.code} · ${assignment.title}`;
    // ONE NAVIGATION (ASSIGNMENT-WORKSPACE, 2026-10-05): the main button is this stage's next step, chosen by state, or none once
    // the paper is printed (revising it is then an ordinary action). The versions current print jobs print lead (a rebuilt quiz's
    // A2–D2); the ones they replaced fold, as on the assignment card.
    const printedLabels = life?.printed_versions || [];
    const current = printedLabels.length ? versions.filter(v => printedLabels.includes(v.label)) : versions;
    const earlier = versions.filter(v => !current.includes(v));
    const printedJobs = jobs.filter(countedJob).length;
    const next = !versions.length ? 'paper' : !printedJobs ? 'print' : '';
    // A MAKEUP VERSION WAITING TO BE APPROVED (ASSIGNMENT-WORKSPACE, 2026-11-05): the tab carries its mark, so the way to it is in view.
    const makeupWaits = (context.needs_you || []).find(item => item.key === 'makeup');
    const pages = list => { const counts = [...new Set(list.map(v => v.page_count))]; return counts.length === 1 ? `${plural(counts[0], 'page')}${list.length > 1 ? ' each' : ''}` : list.map(v => `${v.label}: ${plural(v.page_count, 'page')}`).join(', '); };
    const said = versions.length ? `${current.length === 1 ? 'Version' : 'Versions'} ${current.map(v => v.label).join(', ')} · ${pages(current)} · ${printedJobs ? plural(printedJobs, 'current print job') : 'not printed yet'}` : 'No paper yet';
    patchChildren(host, `<section class="assignment-block"><h2>Paper and printing</h2><p class="assignment-lead">${escape(said)}${canvasFact ? ` · <span data-workspace-canvas-fact>${escape(canvasFact)}</span>` : ''}</p>
      <div class="assignment-buttons"><button type="button" class="button${next === 'paper' ? ' primary' : ''}" data-workspace-paper>Add or revise paper</button><button type="button" class="button${next === 'print' ? ' primary' : ''}" data-workspace-print>Print copies</button>${makeupWaits ? `<button type="button" class="button${next ? '' : ' primary'}" data-workspace-makeup>${escape(makeupWaits.label)}…</button>` : ''}<button type="button" class="button" data-workspace-seating>Seating and arrangements</button>${life ? `<button type="button" class="text-button" data-workspace-given-on>${life.exam_date ? `Given on ${escape(wallDate(life.exam_date))} · Change` : 'Set date given'}</button>` : ''}</div>
      ${earlier.length ? `<details class="disclosure"><summary>Earlier versions</summary><p>${escape(earlier.map(v => `${v.label} · ${plural(v.page_count, 'page')}`).join('; '))}: replaced by the versions printed now.</p></details>` : ''}
      ${life?.quiz_draft_id ? `<p><a class="link-arrow" href="#documents/quiz/${encodeURIComponent(life.quiz_draft_id)}">Approved quiz and source</a></p>` : ''}
      <details class="disclosure"><summary>Assignment tools</summary><div class="assignment-buttons">
        <button type="button" class="text-button" data-workspace-rename>Rename…</button>
        ${canvas?.configured ? `<button type="button" class="text-button" data-workspace-canvas aria-label="${escape(`${onCanvas ? 'Update' : 'Put'} ${name} on Canvas`)}">${onCanvas ? 'Update on Canvas…' : 'Put on Canvas…'}</button>` : ''}
        ${context.plan_id && !makeupWaits ? '<button type="button" class="text-button" data-workspace-makeup>Draft makeup version…</button>' : ''}
      </div></details></section>
      <section class="assignment-block"><h2>Print jobs</h2><div class="document-export-list">${jobGroups(jobs.filter(currentJob)) || '<p>No current print jobs.</p>'}</div>
      <details class="disclosure"><summary>Earlier print jobs</summary><div class="document-export-list">${jobGroups(jobs.filter(b => !currentJob(b))) || '<p>No earlier print jobs.</p>'}</div></details></section>`);
    const open = step => wizard.openForAssignment(assignment, step, context.term_id);
    host.querySelector('[data-workspace-paper]').onclick = () => open('version');
    host.querySelector('[data-workspace-print]').onclick = () => open('print');
    host.querySelector('[data-workspace-seating]').onclick = () => open('seating');
    host.querySelector('[data-workspace-rename]').onclick = () => renameDialog(assignment, onChanged);
    // Property handlers only: patchChildren keeps these buttons across refreshes, so addEventListener would bind them again each time.
    const givenOn = host.querySelector('[data-workspace-given-on]');
    if (givenOn) givenOn.onclick = () => void givenOnDialog(givenOn, assignment, context.term_id, onChanged);
    const toCanvas = host.querySelector('[data-workspace-canvas]');
    if (toCanvas) toCanvas.onclick = () => {
      void canvasPlacement.open({termId: context.term_id, assignmentId: assignment.id, title: name});
      // Where it stands is read again once the window closes.
      document.querySelector('#dialog')?.addEventListener('close', () => void Promise.resolve(onChanged()).catch(error => toast(error.message, true)), {once: true});
    };
    const makeup = host.querySelector('[data-workspace-makeup]');
    if (makeup) makeup.onclick = () => void import('./makeup-drafts.js').then(({openMakeupDrafts}) => {
      void openMakeupDrafts({api, escape, toast, showDialog, dialogHeader}, {planId: context.plan_id, title: name});
      // Where it stands is read again once the window closes.
      document.querySelector('#dialog')?.addEventListener('close', () => void Promise.resolve(onChanged()).catch(error => toast(error.message, true)), {once: true});
    });
    host.querySelectorAll('[data-batch]').forEach(node => node.onclick = () => detail(node.dataset.batch).catch(error => toast(error.message, true)));
  }
  // ASSIGNMENT-WORKSPACE: the Assignments list's header actions, for the course its filter shows (as this page's header did).
  return { render, renderWorkspace,
    // Print at Home from the file viewer (DOCUMENTS "SEND TO PRINTER", 2026-10-18): a job's, a stack's or a room's PDF opened there.
    printAtHome: ({kind, batchIds, chartId, subtitle}, back) => sendToPrinter({kind, batches: batchIds.map(id => ({id})), chartId, subtitle}, back)
      .catch(error => toast(error.message, true)),
    create: (definitionId = "") => wizard.open({definitionId}),
    draft: (definitionId = "") => quizDrafts.openCreate(definitionId).catch(error => toast(error.message, true)) };
}
