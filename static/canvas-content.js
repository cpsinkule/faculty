// @ts-check
/**
 * Canvas modules and pages for a term's sections (canvas/README.md "Modules and pages"): Faculty's one copy, edited here and
 * pushed to every section's Canvas course. The course Home's `#course/<group>/home/canvas` renders into [data-canvas-content-host].
 *
 * The copy on screen is browser memory until Save (a new revision, `base_revision` checked on the server). Push opens a preview of
 * what each course would receive; the instructor's ticks (Replace, Remove) and pairings ("Same as…") change the preview, and only
 * Push sends the previewed plan's fingerprint. Page bodies are edited in a sandboxed frame (no scripts run; Canvas's own markup,
 * embedded videos included, is kept as it is); its HTML view edits the markup itself.
 *
 * FILES (canvas FILES): "File…" among a module's new items keeps a file in Faculty (saved at once, the term's) and adds an item
 * naming it; Canvas receives the file when the instructor pushes. "Replace…" on a file item keeps a corrected file under the same
 * name in Canvas.
 */
import { createCanvasPush, plural, sectionsText, TYPES, withCanvasSignIn } from "./canvas-push.js";
import { actionMenuMarkup, bindActionMenu, dialogSession, emptyMarkup, registerLeaveGuard, rememberedOpen, setBusy } from "./ui.js";

const LONG = 180000; // starting the copy reads a whole Canvas course
const clone = (value) => JSON.parse(JSON.stringify(value));
const localTime = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const isoTime = (local) => (local ? new Date(local).toISOString().replace(/\.\d{3}Z$/, "Z") : null);
const DAY = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const DAY_YEAR = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" });
const TIME = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
/** A day as the page says it, "Oct 10" (the year only when it is another year's). @param {string|Date} value */
const dayText = (value) => {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "" : (d.getFullYear() === new Date().getFullYear() ? DAY : DAY_YEAR).format(d);
};
/** A moment as the page says it, "Oct 10, 4:15 PM": no seconds, and the year only when it is another year's. @param {string|Date} value */
const when = (value) => {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "" : `${dayText(d)}, ${TIME.format(d)}`;
};
const FILES_ROOT = "course files"; // Canvas's name for the top folder of a course's Files (canvas/content.py FILES_ROOT)
const MAX_FILE = 100 * 1024 * 1024;
const sizeText = (/** @type {number} */ size) => (size < 1024 ? `${size} bytes` : size < 1024 * 1024 ? `${Math.round(size / 1024)} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`);
/** A file item's place in a course's Files without the top folder: "Handouts/Review 2.pdf". @param {string} path */
const inFiles = (path) => (path.startsWith(`${FILES_ROOT}/`) ? path.slice(FILES_ROOT.length + 1) : path);

/** @param {{api: any, escape: (v: any) => string, icon: (name: string) => string, toast: (m: string, o?: any) => void,
 *  showDialog: (html: string) => void, dialogHeader: (t: string, s?: string) => string}} deps */
export function createCanvasContent({ api, escape, icon, toast, showDialog, dialogHeader, ask }) {
  const e = escape;
  const pushWindow = createCanvasPush({ api, escape, toast, showDialog, dialogHeader, ask });
  /** @type {any} */ let state = null; // {group, termId, view, draft, dirty, tab, openPage, requestId, saving, html}
  let generation = 0;
  // The read under way, keyed by group and section: the course page renders this twice per navigation (FRONTEND.md "One read per
  // navigation"), and a section's view is a live Canvas read, so an unforced render joins it instead of reading again.
  /** @type {{key: string, node: HTMLElement, promise: Promise<void>} | null} */
  let loading = null;

  const host = () => /** @type {HTMLElement|null} */ (document.querySelector("[data-canvas-content-host]"));
  const active = (owner) => state === owner && owner.node === host() && owner.node.isConnected;
  const newKey = (prefix) => `${prefix}-${crypto.randomUUID()}`;

  // Unsaved changes keep the page (another address, Back, closing the tab) until the instructor lets them go.
  registerLeaveGuard(() => (state?.dirty && host() ? (state.section !== null
    ? "Your changes to this section’s Canvas course are not pushed. Leave without pushing them?"
    : "Your changes to the Canvas modules and pages are not saved. Leave without saving?") : null));

  const direct = () => state?.section !== null && state?.section !== undefined;
  /** The Canvas courses of what is on screen: the term's (its copy) or the one section's (edited directly). */
  const coursesOf = () => (direct() ? [state.view.course] : state.view.courses || []);

  /**
   * Mount for a course group (the course page calls this after drawing the host): the term's copy, or with a section number that
   * section's own Canvas course edited directly (canvas/section_content.py: read from Canvas, the edit is browser memory, pushed).
   * @param {string} groupId @param {boolean} [force] @param {number|null} [section]
   */
  async function render(groupId, force = false, section = null) {
    const node = host();
    if (!node) return;
    section = section ?? null;
    if (!force && state?.group === groupId && state.section === section && state.node === node && node.firstElementChild) return;
    const key = `${groupId}:${section ?? ""}`;
    if (!force && loading?.key === key && loading.node === node) return loading.promise;
    const promise = load(node, groupId, force, section);
    loading = { key, node, promise };
    try { await promise; } finally { if (loading?.promise === promise) loading = null; }
  }

  /** @param {HTMLElement} node @param {string} groupId @param {boolean} force @param {number|null} section */
  async function load(node, groupId, force, section) {
    const run = ++generation;
    const current = () => run === generation && node === host() && node.isConnected;
    node.innerHTML = `<p class="loading-state">${section === null ? "Opening Canvas modules and pages…" : `Reading Section ${section}’s Canvas course…`}</p>`;
    try {
      const place = await api(`/api/canvas/content/group/${encodeURIComponent(groupId)}${section === null ? "" : `?section=${encodeURIComponent(section)}`}`);
      if (!current()) return;
      const termId = place.term_id;
      const view = section === null ? await api(`/api/canvas/content/${encodeURIComponent(termId)}`)
        : await withCanvasSignIn(() => api(`/api/canvas/sections/${encodeURIComponent(place.section_id)}/content`, "GET", undefined, {}, LONG), { api, showDialog, dialogHeader, escape, current });
      if (!current()) return;
      if (!view) { node.innerHTML = '<p class="cc-empty">Sign in to Canvas to open this section’s course.</p>'; return; }
      const same = state?.termId === termId && state.section === section;
      const keep = same && state.dirty && !force ? state : null;
      state = keep ? { ...keep, group: groupId, node, view } : { group: groupId, termId, section, sectionId: place.section_id || "", node, view,
        draft: view.content ? clone(view.content) : null, dirty: false, tab: same ? state.tab : "modules", openPage: null, requestId: "", saving: false, html: false,
        readAt: new Date() };
      draw();
      if (view.push?.status === "running") watch(view.push.id);
      // `?push=1` (the Canvas page's next step) opens the push window once, then leaves the address.
      const [path, query = ""] = location.hash.split("?");
      const params = new URLSearchParams(query);
      if (params.get("push") === "1") {
        params.delete("push");
        history.replaceState(history.state, "", `${path}${params.toString() ? `?${params}` : ""}`);
        if (view.content && !state.dirty && view.push?.status !== "running") void openPush();
      }
    } catch (error) {
      if (!current()) return;
      node.innerHTML = `<div class="empty-state">${icon("info")}<h3>${section === null ? "Canvas modules and pages could not be opened" : `Section ${e(section)}’s Canvas course could not be opened`}</h3><p role="alert">${e(error?.message || "Something went wrong.")}</p><button type="button" class="button" data-cc-retry>Try again</button></div>`;
      /** @type {HTMLElement} */ (node.querySelector("[data-cc-retry]")).onclick = () => { void render(groupId, true, section); };
    }
  }

  /** The page or copy assignment a module item is published with (Canvas publishes them together), or null.
   * @param {any} item */
  function contentOf(item) {
    const draft = state.draft;
    if (item.type === "Page") return draft.pages.find((/** @type {any} */ p) => (p.id || p.ref) === item.page) || null;
    const key = item.content?.assignment;
    return key ? (draft.assignments || []).find((/** @type {any} */ a) => (a.id || a.ref) === key) || null : null;
  }

  function touch() {
    // Canvas keeps a page's or an assignment's module items published exactly as the page or assignment is (canvas/content.py
    // normalize): the draft shows them the same way.
    for (const module of state.draft.modules || []) {
      for (const item of module.items) {
        const content = contentOf(item);
        if (content) item.published = !!content.published;
      }
    }
    state.dirty = true;
    state.editRevision = (state.editRevision || 0) + 1;
    state.requestId = "";
    const bar = host()?.querySelector("[data-cc-savebar]");
    if (bar) bar.outerHTML = direct() ? directBar() : saveBar();
    bindSaveBar();
  }

  // ---- drawing ---------------------------------------------------------------------------------------------------------------
  const TABS = [["modules", "Modules"], ["pages", "Pages"], ["assignments", "Assignments"]];

  /** Modules | Pages | Assignments: the shared sub-tabs (DESIGN-LANGUAGE §4), each with its count. */
  function tabsMarkup() {
    const counts = { modules: state.draft.modules.length, pages: state.draft.pages.length, assignments: assignmentCount() };
    return `<div class="cc-tabs sub-tabs" role="tablist" aria-label="Modules, pages and assignments">${TABS.map(([key, label]) =>
      `<button type="button" role="tab" class="cc-tab" aria-selected="${state.tab === key}" data-cc-tab="${key}">${label} <b>${counts[key]}</b></button>`).join("")}</div>`;
  }

  const panelMarkup = () => (state.tab === "modules" ? modulesMarkup() : state.tab === "pages" ? pagesMarkup() : assignmentsMarkup());

  /** Writes the page and binds it. A list that scrolls in its own box (the pages, the assignments) keeps its place. */
  function paint(node, notes) {
    const kept = /** @type {HTMLElement|null} */ (node.querySelector("[data-cc-scroll]"));
    const keep = kept ? { tab: kept.dataset.ccScroll, top: kept.scrollTop } : null;
    node.innerHTML = `${direct() ? directBar() : saveBar()}<div class="cc-notes" data-cc-push-alert>${pushAlert()}</div>${notes}
      ${tabsMarkup()}<div class="cc-panel" data-cc-panel>${panelMarkup()}</div>`;
    const list = /** @type {HTMLElement|null} */ (node.querySelector("[data-cc-scroll]"));
    if (list && keep && keep.tab === list.dataset.ccScroll) list.scrollTop = keep.top;
    bindSaveBar();
    bindActionMenu(node);
    for (const tab of node.querySelectorAll("[data-cc-tab]")) /** @type {HTMLElement} */ (tab).onclick = () => { state.tab = /** @type {HTMLElement} */ (tab).dataset.ccTab; draw(); };
    if (state.tab === "modules") bindModules(node); else if (state.tab === "pages") bindPages(node); else bindAssignments(node);
  }

  function draw() {
    const node = host();
    if (!node || !state || !active(state)) return;
    const { view } = state;
    const canvas = view.canvas || {};
    // The header band carries no lead here (the work starts under it): only a page with nothing to edit yet says what it is for.
    const intro = '<p class="cc-lead">One copy of this term’s Canvas modules, pages and assignments. Edit it here, then push it to every section’s Canvas course.</p>';
    if (!canvas.configured) {
      node.innerHTML = `${intro}<div class="callout" data-tone="warning"><p>Canvas is not set up for this Faculty instance.</p></div>`;
      return;
    }
    if (direct()) {
      drawDirect(node);
      return;
    }
    if (!view.courses.length) {
      // The step is done on a section tab (its work list's Canvas line): link to the first section that needs it.
      const first = (view.unlinked || [])[0]?.number ?? (view.sections || [])[0]?.number;
      node.innerHTML = `${intro}${emptyMarkup({ icon: "link", title: "No Canvas courses linked yet", body: "Link each section’s Canvas course on its section tab; this page fills in once one is linked.",
        action: first !== undefined && first !== null ? { label: `Open Section ${first}`, href: `#course/${encodeURIComponent(state.group)}/section/${encodeURIComponent(first)}` } : null })}`;
      return;
    }
    if (!view.content) {
      node.innerHTML = `${intro}<section class="card cc-start"><h3>Start from a section’s Canvas course</h3>
        <p>Choose the section whose Canvas course already looks the way every section should. Faculty copies its modules, pages and assignments as they are now and matches the other sections to it by name; nothing changes in Canvas until you push, and the first push goes to one section so you can check it there.</p>
        <label class="field">Start from<select data-cc-source>${view.courses.map((c) => `<option value="${e(c.course_id)}">${e(sectionsText(c))} · ${e(c.name)}</option>`).join("")}</select></label>
        <div class="cc-actions"><button type="button" class="button primary" data-cc-start>${icon("download")}Start from this course</button></div>
        <p class="form-error" role="alert" hidden></p></section>${unlinkedNote()}`;
      /** @type {HTMLElement} */ (node.querySelector("[data-cc-start]")).onclick = startCopy;
      return;
    }
    paint(node, `${unlinkedNote()}${guideNote()}`);
  }

  /** One section's own Canvas course (section mode): where it stands, the one bar, and the same editors as the copy. */
  function drawDirect(node) {
    const { view } = state;
    const shared = view.course.sections.length > 1
      ? `<div class="callout" data-tone="info"><p>${e(sectionsText(view.course))} share this Canvas course: a change here reaches each of them.</p></div>` : "";
    // One muted sentence says what the mark means and what a change here leads to: no box, nothing to open (§1.13).
    const managed = (view.managed || []).length
      ? `<p class="cc-managed" data-cc-managed>What is marked “term’s copy” is kept in step for every section by <a href="#course/${e(encodeURIComponent(state.group))}/home/canvas">All sections</a>. Its next push asks about a change made here, undoes a move, and makes again what was deleted here unless the term’s copy drops it too.</p>` : "";
    const skipped = (view.skipped || []).length
      ? `<details class="disclosure"><summary><span>Not shown here</span><span class="disclosure-hint">${view.skipped.length}</span></summary><div class="disclosure-body"><p class="field-hint">Faculty’s editor cannot show these module items; they stay in Canvas as they are.</p><ul class="cc-list">${view.skipped.map((x) => `<li>${e(x)}</li>`).join("")}</ul></div></details>` : "";
    paint(node, `${shared}${managed}${skipped}`);
  }

  /** The bar, section mode: what the page holds, and its one primary (Review and push… once something changed). */
  function directBar() {
    const read = state.readAt ? TIME.format(state.readAt) : "";
    const status = state.dirty
      ? '<span class="chip" data-tone="warning">Changes not pushed yet</span><span class="cc-status-rest">They go to this section’s Canvas course only.</span>'
      : `As in Canvas${read ? ` · read ${e(read)}` : ""} · ${e(state.view.course.name)}`;
    const actions = state.dirty
      ? `<button type="button" class="button" data-cc-discard>Discard changes</button><button type="button" class="button primary" data-cc-push-open>${icon("route")}Review and push…</button>`
      : `<button type="button" class="button" data-cc-reread>${icon("refresh")}Read Canvas again</button>`;
    return `<div class="cc-savebar" data-cc-savebar><div class="cc-state"><p class="cc-status">${status}</p>${pushLine()}</div><div class="cc-actions">${actions}</div></div>`;
  }

  /** In section mode, what the term's copy keeps in step is marked quietly (most of a course is): never a framed chip. */
  const isManaged = (entry) => direct() && (state.view.managed || []).includes(entry.id);
  const managedMark = (entry) => (isManaged(entry) ? '<span class="cc-mark">term’s copy</span>' : "");

  function unlinkedNote() {
    const missing = state.view.unlinked || [];
    return missing.length ? `<div class="callout" data-tone="info"><p>${e(plural(missing.length, "section"))} without a Canvas course (${e(missing.map((s) => s.number).join(", "))}) ${missing.length === 1 ? "is" : "are"} left out. Link ${missing.length === 1 ? "it" : "them"} on the section tab.</p></div>` : "";
  }

  /** Where the term stands after the copy is started: the first push goes to one section, then the rest (one line, no action:
   * the bar's Push to Canvas… is the page's one primary). */
  function guideNote() {
    const { courses, pushed_courses: pushed = [], push } = state.view;
    if (push?.status === "running" || courses.length < 2 || pushed.length >= courses.length) return "";
    const text = pushed.length
      ? `${plural(pushed.length, "Canvas course")} of ${courses.length} ${pushed.length === 1 ? "has" : "have"} Faculty’s copy. Once you have checked ${pushed.length === 1 ? "it" : "them"} in Canvas, push to the rest.`
      : "Nothing has been pushed yet. Push to one section first (the push window starts with one ticked), check it in Canvas, then push to the rest.";
    return `<div class="callout" data-tone="info" data-cc-guide><p>${e(text)}</p></div>`;
  }

  // ---- the last push: one quiet line of the bar when it went through, a notice under the bar when it needs a look ------------------
  function pushCounts(push) {
    const courses = Object.values(push.progress?.courses || {});
    return { done: courses.reduce((n, c) => n + (c.done || 0), 0), failed: courses.reduce((n, c) => n + (c.failed || 0), 0),
      total: courses.reduce((n, c) => n + (c.total || 0), 0) };
  }

  const pushQuiet = (push) => Boolean(push) && push.status === "done" && !pushCounts(push).failed;

  /** "Check it in Canvas: Section 507 ↗ · …": the modules of each course the push wrote to. */
  function checkLinks(push) {
    const url = state.view.canvas?.url;
    const ids = Object.keys(push.progress?.courses || {});
    if (!url || !ids.length) return "";
    const names = Object.fromEntries(coursesOf().map((c) => [c.course_id, sectionsText(c)]));
    return `Check it in Canvas: ${ids.map((cid) => `<a href="${e(url)}/courses/${e(cid)}/modules" target="_blank" rel="noopener noreferrer">${e(names[cid] || `Course ${cid}`)} ↗</a>`).join(" · ")}`;
  }

  function pushLine() {
    const push = state.view.push;
    if (!pushQuiet(push)) return "";
    // A revision saved after the push (by a Save here, or by Faculty when a column or a due date changed) is not in Canvas by it.
    const since = !direct() && state.view.saved_at && new Date(state.view.saved_at) > new Date(push.created_at) ? ' · <strong>the copy has changed since</strong>' : "";
    const links = checkLinks(push);
    return `<p class="cc-pushline" data-cc-push-note>Last push ${e(when(push.finished_at || push.created_at))}: ${e(plural(pushCounts(push).done, "change"))}${since}${links ? ` · ${links}` : ""}</p>`;
  }

  function pushAlert() {
    const push = state.view.push;
    if (!push || pushQuiet(push)) return "";
    const { done, failed, total } = pushCounts(push);
    const at = when(push.finished_at || push.created_at);
    const words = push.status === "running" ? `Pushing to Canvas: ${done + failed} of ${total} changes` :
      push.status === "done" ? `Last push ${at}: ${plural(done, "change")}, ${plural(failed, "failure")}` :
      push.status === "interrupted" ? `The push of ${at} was interrupted (Faculty restarted). ${direct() ? "Read Canvas again and push once more." : "Push again: the preview reads Canvas as it is now."}` :
      push.status === "cancelled" ? `The push of ${at} was cancelled after ${plural(done, "change")}.` : `The push of ${at} stopped: ${push.progress?.error || "Canvas refused the session."}`;
    const links = push.status === "running" ? "" : checkLinks(push);
    const errors = failed && push.status !== "running" ? Object.values(push.progress.courses).flatMap((c) => c.errors) : [];
    return `<div class="callout cc-push-note" data-tone="${push.status === "running" ? "info" : "warning"}" data-cc-push-note><p>${e(words)}</p>${links ? `<p class="cc-open">${links}</p>` : ""}${errors.length ? `<details class="disclosure"><summary><span>What failed</span></summary><div class="disclosure-body"><ul>${errors.map((x) => `<li><strong>${e(x.label)}</strong>: ${e(x.message)}</li>`).join("")}</ul></div></details>` : ""}</div>`;
  }

  /** The push as it stands now, in its two places (the bar's line, the notice under the bar). */
  function refreshPush() {
    const node = host();
    const bar = node?.querySelector("[data-cc-savebar]"), slot = node?.querySelector("[data-cc-push-alert]");
    if (!bar || !slot) { if (node) draw(); return; }
    bar.outerHTML = direct() ? directBar() : saveBar();
    slot.innerHTML = pushAlert();
    bindSaveBar();
  }

  /** The bar: where the copy stands on the left, the page's one next step on the right (Save while there are changes, else
   * Push to Canvas…). It stays in view while the page scrolls, so neither is ever a scroll away. */
  function saveBar() {
    const { view } = state;
    const status = state.dirty
      ? '<span class="chip" data-tone="warning">Unsaved changes</span><span class="cc-status-rest">Save them, then push to send them to Canvas.</span>'
      : `Saved${view.saved_at ? ` ${e(when(view.saved_at))}` : ""} · revision ${e(view.revision)}`;
    const actions = state.dirty
      ? `<button type="button" class="button" data-cc-discard>Discard changes</button><button type="button" class="button primary" data-cc-save ${state.saving ? "disabled" : ""}>${state.saving ? "Saving…" : "Save"}</button>`
      : `<button type="button" class="button primary" data-cc-push-open>${icon("route")}Push to Canvas…</button>`;
    return `<div class="cc-savebar" data-cc-savebar><div class="cc-state"><p class="cc-status">${status}</p>${pushLine()}</div><div class="cc-actions">${actions}</div></div>`;
  }

  function bindSaveBar() {
    const node = host();
    if (!node) return;
    const save = /** @type {HTMLButtonElement|null} */ (node.querySelector("[data-cc-save]"));
    if (save) save.onclick = () => { void saveCopy(); };
    const discard = /** @type {HTMLElement|null} */ (node.querySelector("[data-cc-discard]"));
    if (discard) discard.onclick = () => { if (confirm(direct() ? "Discard your changes to this section’s Canvas course?" : "Discard your unsaved changes to the modules and pages?")) { state.draft = clone(state.view.content); state.dirty = false; draw(); } };
    const reread = /** @type {HTMLElement|null} */ (node.querySelector("[data-cc-reread]"));
    if (reread) reread.onclick = () => { void render(state.group, true, state.section); };
    const push = /** @type {HTMLElement|null} */ (node.querySelector("[data-cc-push-open]"));
    if (push) push.onclick = () => { void openPush(); };
    // The lists that stay beside their editor sit under the bar, however tall it is (one line, or two with the last push).
    const bar = /** @type {HTMLElement|null} */ (node.querySelector("[data-cc-savebar]"));
    if (bar) node.style.setProperty("--cc-bar", `${Math.ceil(bar.getBoundingClientRect().height)}px`);
  }

  /** Brings the editor just opened into view under the bar; the list beside it keeps its own place. */
  function reveal() {
    const node = host();
    const editor = /** @type {HTMLElement|null} */ (node?.querySelector(".cc-page-editor"));
    if (!node || !editor) return;
    const bar = node.querySelector("[data-cc-savebar]")?.getBoundingClientRect().bottom || 0;
    const top = editor.getBoundingClientRect().top;
    if (top < bar || top > innerHeight * 0.6) editor.scrollIntoView({ block: "start" });
  }

  // ---- modules ---------------------------------------------------------------------------------------------------------------
  function sectionBoxes(entry, key) {
    const sections = state.view.sections || [];
    if (sections.length < 2) return "";
    const chosen = new Set(entry.sections || []);
    return `<fieldset class="cc-sections"><legend>Sections</legend><label class="checkbox-field"><input type="checkbox" data-cc-all="${e(key)}" ${chosen.size ? "" : "checked"}>All</label>${sections.map((s) => `<label class="checkbox-field"><input type="checkbox" data-cc-section="${e(key)}" value="${e(s.id)}" ${chosen.has(s.id) ? "checked" : ""} ${chosen.size ? "" : "disabled"}>${e(s.number)}</label>`).join("")}</fieldset>`;
  }

  /** What an item shows, beside its title: the page, the address, the file, or the name it goes by in each course when that is
   * not its title (most quizzes and assignments are titled by their name: said once). */
  function targetMarkup(item, key) {
    if (item.type === "Page") return `<select data-cc-item-page="${key}" aria-label="Page">${state.draft.pages.map((p) => `<option value="${e(p.id || p.ref)}" ${item.page === (p.id || p.ref) ? "selected" : ""}>${e(p.title)}</option>`).join("")}</select>`;
    if (["ExternalUrl", "ExternalTool"].includes(item.type)) return `<input type="url" value="${e(item.external_url || "")}" data-cc-item-url="${key}" aria-label="Address of ${e(item.title)}">`;
    if (item.type === "File" && item.content) return fileMarkup(item, key);
    const kind = (TYPES[item.type] || item.type).toLowerCase();
    const name = contentOf(item)?.name ?? item.content?.name ?? "";
    return name && String(name).trim() !== String(item.title).trim() ? `<span class="cc-content" title="The ${e(kind)} of this name in each course">${e(name)}</span>` : "";
  }

  function modulesMarkup() {
    const modules = state.draft.modules;
    const itemRow = (module, item, mi, ii) => {
      const key = `${mi}:${ii}`;
      const content = contentOf(item);
      const together = content ? ` title="Canvas publishes this item and its ${item.type === "Page" ? "page" : "assignment"} together"` : "";
      const target = targetMarkup(item, key);
      // One line an item: what it is, its title, what it shows, Published, and its place. The rarer moves are in its menu.
      const menu = actionMenuMarkup({ id: `cc-item-menu-${mi}-${ii}`, label: `More for ${item.title}`, items: [
        { label: "Indent", data: { ccItemIndent: `${key}:1` }, disabled: item.indent >= 5 },
        { label: "Outdent", data: { ccItemIndent: `${key}:-1` }, disabled: !item.indent },
        { label: "Move to another module…", data: { ccItemModule: key }, disabled: modules.length < 2 },
        { label: "Remove from this module", data: { ccItemRemove: key }, danger: true },
      ] });
      return `<li class="cc-item" data-indent="${item.indent}" data-type="${e(item.type)}">
        <span class="cc-item-kind">${e(TYPES[item.type] || item.type)}${managedMark(item)}</span>
        <input type="text" class="cc-item-title" value="${e(item.title)}" maxlength="255" data-cc-item-title="${key}" aria-label="Title">
        ${target ? `<span class="cc-item-target">${target}</span>` : ""}
        <label class="checkbox-field cc-published"${together}><input type="checkbox" data-cc-item-published="${key}" ${(content ? content.published : item.published) ? "checked" : ""}>Published</label>
        <span class="cc-row-actions">
          <button type="button" class="icon-button" data-cc-item-move="${key}:-1" aria-label="Move up" ${ii ? "" : "disabled"}>↑</button>
          <button type="button" class="icon-button" data-cc-item-move="${key}:1" aria-label="Move down" ${ii < module.items.length - 1 ? "" : "disabled"}>↓</button>
          ${menu}
        </span></li>`;
    };
    const assignments = state.draft.assignments || [];
    const kinds = `<option value="SubHeader">Text header</option><option value="Page">Page</option><option value="ExternalUrl">Web link</option><option value="File">File…</option>${assignments.length ? `<optgroup label="An assignment">${assignments.map((a) => `<option value="assignment:${e(a.id || a.ref)}">${e(a.name)}</option>`).join("")}</optgroup>` : ""}`;
    const card = (module, mi) => {
      // State before metadata (DESIGN-LANGUAGE §1.5): unpublished and limited to some sections are marked; a module that is
      // already open to students says nothing of when it opened.
      const marks = `${module.published ? "" : '<span class="chip" data-tone="neutral">Unpublished</span>'}${module.sections?.length ? '<span class="chip" data-tone="neutral">Some sections</span>' : ""}`;
      const opens = module.unlock_at && new Date(module.unlock_at) > new Date() ? ` · opens ${when(module.unlock_at)}` : "";
      return `<details class="cc-module" data-cc-module="${mi}" ${state.openModules?.has(module.id || module.ref) ? "open" : ""}>
      <summary><span class="cc-module-name">${e(module.name)}</span>${marks}<span class="cc-module-facts">${e(`${plural(module.items.length, "item")}${opens}`)}${managedMark(module)}</span></summary>
      <div class="cc-module-body">
        <div class="cc-module-fields">
          <label class="field cc-grow">Name<input type="text" value="${e(module.name)}" maxlength="255" data-cc-module-name="${mi}"></label>
          <label class="field">Opens <span class="field-optional">optional</span><input type="datetime-local" value="${e(localTime(module.unlock_at))}" data-cc-module-unlock="${mi}"></label>
          <div class="cc-checks"><label class="checkbox-field"><input type="checkbox" data-cc-module-published="${mi}" ${module.published ? "checked" : ""}>Published</label>
            <label class="checkbox-field"><input type="checkbox" data-cc-module-sequential="${mi}" ${module.require_sequential_progress ? "checked" : ""}>Students go in order</label></div>
          ${sectionBoxes(module, `m${mi}`)}
          <span class="cc-module-tools">
            <button type="button" class="icon-button" data-cc-module-move="${mi}:-1" aria-label="Move module up" title="Move module up" ${mi ? "" : "disabled"}>↑</button>
            <button type="button" class="icon-button" data-cc-module-move="${mi}:1" aria-label="Move module down" title="Move module down" ${mi < modules.length - 1 ? "" : "disabled"}>↓</button>
            <button type="button" class="button danger" data-cc-module-remove="${mi}">Delete module</button>
          </span>
        </div>
        <ul class="cc-items">${module.items.map((item, ii) => itemRow(module, item, mi, ii)).join("") || '<li class="cc-empty">No items yet.</li>'}</ul>
        <div class="cc-actions cc-add">
          <select data-cc-add-type="${mi}" aria-label="Kind of item">${kinds}</select>
          <button type="button" class="button" data-cc-add-item="${mi}">${icon("plus")}Add item</button>
        </div></div></details>`;
    };
    return `<div class="cc-modules">${modules.map(card).join("") || '<p class="cc-empty">No modules yet.</p>'}</div>
      <div class="cc-actions"><button type="button" class="button" data-cc-add-module>${icon("plus")}Add module</button></div>`;
  }

  // The module editor's handlers are bound ONCE to each host element and read the draft as it is when they run. Bound at every
  // draw (as they were), each redraw added another set, so one click on Add item added several and Move down undid itself.
  const boundHosts = new WeakSet();

  function bindModules(node) {
    state.openModules ||= new Set();
    for (const details of node.querySelectorAll("[data-cc-module]")) {
      details.addEventListener("toggle", () => {
        const module = state.draft.modules[Number(/** @type {HTMLElement} */ (details).dataset.ccModule)];
        if (!module) return;
        const key = module.id || module.ref;
        if (/** @type {HTMLDetailsElement} */ (details).open) state.openModules.add(key); else state.openModules.delete(key);
      });
    }
    if (boundHosts.has(node)) return;
    boundHosts.add(node);
    const at = (value) => value.split(":").map(Number);
    const redraw = () => { touch(); draw(); };
    const live = () => (state && state.tab === "modules" && state.draft ? state.draft.modules : null);
    node.addEventListener("input", (event) => {
      const modules = live();
      if (!modules) return;
      const t = /** @type {HTMLInputElement} */ (event.target), d = t.dataset;
      if (d.ccModuleName) { modules[Number(d.ccModuleName)].name = t.value; touch(); }
      else if (d.ccItemTitle) { const [m, i] = at(d.ccItemTitle); modules[m].items[i].title = t.value; touch(); }
      else if (d.ccItemUrl) { const [m, i] = at(d.ccItemUrl); modules[m].items[i].external_url = t.value; touch(); }
    });
    node.addEventListener("change", (event) => {
      const modules = live();
      if (!modules) return;
      const t = /** @type {HTMLInputElement} */ (event.target), d = t.dataset;
      if (d.ccModuleUnlock) { modules[Number(d.ccModuleUnlock)].unlock_at = isoTime(t.value); touch(); }
      else if (d.ccModulePublished) { modules[Number(d.ccModulePublished)].published = t.checked; redraw(); }
      else if (d.ccModuleSequential) { modules[Number(d.ccModuleSequential)].require_sequential_progress = t.checked; touch(); }
      else if (d.ccItemPublished) {
        // An item of a page or an assignment publishes it too, as in Canvas: the page's or assignment's own flag changes.
        const [m, i] = at(d.ccItemPublished);
        const content = contentOf(modules[m].items[i]);
        if (content) { content.published = t.checked; redraw(); } else { modules[m].items[i].published = t.checked; touch(); }
      }
      else if (d.ccItemPage) { const [m, i] = at(d.ccItemPage); modules[m].items[i].page = t.value; touch(); }
      else if (d.ccAll || d.ccSection) sectionChange(node, t, (key) => modules[Number(key.slice(1))]);
    });
    node.addEventListener("click", (event) => {
      const modules = live();
      const b = /** @type {HTMLElement} */ (event.target).closest("button");
      if (!modules || !b) return;
      const d = b.dataset;
      if (d.ccItemIndent) { const [m, i, step] = at(d.ccItemIndent); const item = modules[m].items[i]; item.indent = Math.max(0, Math.min(5, item.indent + step)); redraw(); }
      else if (d.ccItemMove) {
        // The focus goes with the item, so pressing again moves it on (and never sends its neighbour back).
        const [m, i, step] = at(d.ccItemMove);
        const items = modules[m].items;
        [items[i], items[i + step]] = [items[i + step], items[i]];
        redraw();
        refocus(node, [`[data-cc-item-move="${m}:${i + step}:${step}"]`, `[data-cc-item-move="${m}:${i + step}:${-step}"]`]);
      }
      else if (d.ccItemRemove) { const [m, i] = at(d.ccItemRemove); modules[m].items.splice(i, 1); redraw(); }
      else if (d.ccItemModule) { const [m, i] = at(d.ccItemModule); openMove(m, i); }
      else if (d.ccItemFile) { const [m, i] = at(d.ccItemFile); openFile(modules[m], modules[m].items[i]); }
      else if (d.ccModuleMove) {
        const [m, step] = at(d.ccModuleMove);
        [modules[m], modules[m + step]] = [modules[m + step], modules[m]];
        redraw();
        refocus(node, [`[data-cc-module-move="${m + step}:${step}"]`, `[data-cc-module-move="${m + step}:${-step}"]`]);
      }
      else if (d.ccModuleRemove) {
        const module = modules[Number(d.ccModuleRemove)];
        if (confirm(direct() ? `Delete the module “${module.name}”? The push deletes it in Canvas (its items’ assignments and pages stay).` : `Delete the module “${module.name}” from Faculty’s copy? The next push offers to delete it in Canvas too (its items’ assignments and pages stay).`)) { modules.splice(Number(d.ccModuleRemove), 1); redraw(); }
      } else if (d.ccAddItem) {
        const module = modules[Number(d.ccAddItem)];
        const type = /** @type {HTMLSelectElement} */ (node.querySelector(`[data-cc-add-type="${d.ccAddItem}"]`)).value;
        if (type.startsWith("assignment:")) {
          const key = type.slice("assignment:".length);
          const assignment = state.draft.assignments.find((a) => (a.id || a.ref) === key);
          module.items.push({ type: "Assignment", title: assignment.name, indent: 0, published: assignment.published, content: { assignment: key } });
          state.openModules.add(module.id || module.ref);
          redraw();
          return;
        }
        if (type === "File") { openFile(module); return; }
        const page = state.draft.pages[0];
        if (type === "Page" && !page) { toast("Add a page on the Pages tab first."); return; }
        module.items.push({ type, title: type === "Page" ? page.title : type === "SubHeader" ? "New header" : "New link", indent: 0, published: true,
          ...(type === "Page" ? { page: page.id || page.ref } : {}), ...(type === "ExternalUrl" ? { external_url: "https://", new_tab: true } : {}) });
        state.openModules.add(module.id || module.ref);
        redraw();
      } else if ("ccAddModule" in d) {
        const ref = newKey("module");
        modules.push({ ref, name: "New module", published: false, unlock_at: null, require_sequential_progress: false, sections: [], items: [] });
        state.openModules.add(ref);
        redraw();
      }
    });
  }

  /** After a move redrew the list: the focus on the first of these controls that can take it. */
  function refocus(node, selectors) {
    for (const selector of selectors) {
      const control = /** @type {HTMLButtonElement|null} */ (node.querySelector(selector));
      if (control && !control.disabled) { control.focus({ preventScroll: true }); return; }
    }
  }

  /** "Move to another module…" in an item's menu: the module is chosen in a small window and the item goes to its end. */
  function openMove(mi, ii) {
    const owner = state;
    const modules = state.draft.modules;
    const item = modules[mi]?.items[ii];
    if (!item) return;
    showDialog(`<form data-cc-move-form novalidate>${dialogHeader("Move to another module", e(item.title))}
      <div class="dialog-body"><label class="field">Module<select name="module">${modules.map((m, n) => (n === mi ? "" : `<option value="${n}">${e(m.name)}</option>`)).join("")}</select></label>
        <p class="field-hint">It goes to the end of that module.</p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Move</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.querySelector("[data-cc-move-form]"));
    const current = dialogSession(form, /** @type {HTMLDialogElement} */ (document.querySelector("#dialog")));
    form.onsubmit = (event) => {
      event.preventDefault();
      if (!active(owner) || !current()) return;
      const to = modules[Number(/** @type {HTMLSelectElement} */ (form.elements.namedItem("module")).value)];
      if (!to || modules[mi]?.items[ii] !== item) return;
      modules[mi].items.splice(ii, 1);
      to.items.push(item);
      state.openModules.add(to.id || to.ref);
      /** @type {HTMLDialogElement|null} */ (document.querySelector("#dialog"))?.close();
      touch();
      draw();
    };
  }

  // ---- files (canvas FILES) --------------------------------------------------------------------------------------------------
  /** What Faculty keeps of a file an item names: from the term's view, or added on this page. @param {string} id */
  const keptFile = (id) => state.added?.[id] || (state.view.files || []).find((/** @type {any} */ f) => f.id === id) || null;

  /** A file item's target: where it is in each course's Files, the kept file it comes from (a download), and Replace. */
  function fileMarkup(item, key) {
    const kept = item.content.file ? keptFile(item.content.file) : null;
    const where = item.content.file
      ? `<span class="cc-content" title="Kept in Faculty. The push puts it in each course’s Files under this name.">${e(inFiles(item.content.name))}${kept ? ` · ${e(sizeText(kept.size))}` : ""}</span>${kept ? `<a class="text-button" href="${e(kept.url)}" download>Download</a>` : ""}`
      : `<span class="cc-content" title="The file of this name in each course">${e(item.content.name)}</span>`;
    return `${where}<button type="button" class="text-button" data-cc-item-file="${key}" aria-label="Replace the file of ${e(item.title)}">Replace…</button>`;
  }

  /** "File…" among a module's new items, or Replace on a file item (`item`): the chosen file is kept in Faculty at once (the
   * term's, canvas/files.py) and the draft names it. Canvas receives it only when the instructor pushes.
   * @param {any} module @param {any} [item] */
  function openFile(module, item = null) {
    const owner = state;
    const name = item ? item.content.name.split("/").pop() : "";
    const folders = [...new Set(state.draft.modules.flatMap((/** @type {any} */ m) => m.items).filter((/** @type {any} */ i) => i.type === "File" && i.content?.name)
      .map((/** @type {any} */ i) => inFiles(i.content.name).split("/").slice(0, -1).join("/")).filter(Boolean))].sort();
    showDialog(`<form data-cc-file-form novalidate>${dialogHeader(item ? "Replace the file" : "Add a file", e(item ? inFiles(item.content.name) : module.name))}
      <div class="dialog-body">
        <label class="field">File<input type="file" name="file" required><span class="field-hint">Up to 100 MB. ${item ? `It keeps the name ${e(name)} in Canvas.` : "Canvas receives it when you push."}</span></label>
        ${item ? "" : `<label class="field">Folder in Canvas’s Files <span class="field-optional">optional</span><input type="text" name="folder" maxlength="200" list="cc-file-folders" placeholder="Top folder" autocomplete="off"></label>
        <datalist id="cc-file-folders">${folders.map((f) => `<option value="${e(f)}">`).join("")}</datalist>`}
        <p class="form-error" role="alert" hidden></p>
      </div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${item ? "Replace" : "Add to the module"}</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.querySelector("[data-cc-file-form]"));
    const current = dialogSession(form, /** @type {HTMLDialogElement} */ (document.querySelector("#dialog")));
    const error = /** @type {HTMLElement} */ (form.querySelector(".form-error"));
    const fail = (/** @type {string} */ message) => { error.textContent = message; error.hidden = !message; };
    form.onsubmit = async (event) => {
      event.preventDefault();
      if (!active(owner) || !current() || form.dataset.saving === "true") return;
      const file = /** @type {HTMLInputElement} */ (form.elements.namedItem("file")).files?.[0];
      if (!file) { fail("Choose a file."); return; }
      if (!file.size) { fail("That file is empty."); return; }
      if (file.size > MAX_FILE) { fail("That file is larger than 100 MB."); return; }
      const parts = item ? [] : String(/** @type {HTMLInputElement} */ (form.elements.namedItem("folder")).value || "").split("/").map((p) => p.trim()).filter(Boolean);
      if (parts.some((p) => p === "." || p === "..") || parts[0]?.toLowerCase() === FILES_ROOT) { fail("Name the folder as it is inside Canvas’s Files, like Handouts or Exams/Reviews."); return; }
      const data = new FormData();
      data.append("file", file, file.name);
      if (item) data.append("name", name);
      const buttons = [...form.querySelectorAll("button")], submit = /** @type {HTMLButtonElement} */ (form.querySelector('button[type="submit"]'));
      fail("");
      form.dataset.saving = "true";
      buttons.forEach((button) => { button.disabled = true; });
      submit.textContent = "Saving the file…";
      try {
        const kept = await api(`/api/canvas/content/${encodeURIComponent(owner.termId)}/files`, "POST", data, {}, LONG);
        if (!active(owner) || !current()) return;
        (owner.added ||= {})[kept.id] = kept;
        if (item) item.content = { name: item.content.name, file: kept.id };
        else module.items.push({ type: "File", title: kept.name, indent: 0, published: true, content: { name: [FILES_ROOT, ...parts, kept.name].join("/"), file: kept.id } });
        state.openModules.add(module.id || module.ref);
        /** @type {HTMLDialogElement|null} */ (document.querySelector("#dialog"))?.close();
        touch();
        draw();
        toast(direct() ? "The file is in your changes. Review and push to send it to Canvas." : "The file is in your changes. Save, then push to send it to Canvas.");
      } catch (failure) {
        if (form.isConnected) fail(/** @type {any} */ (failure)?.message || "The file could not be saved."); else toast(/** @type {any} */ (failure)?.message || "The file could not be saved.", { tone: "danger" });
        form.dataset.saving = "false";
        buttons.forEach((button) => { button.disabled = false; });
        submit.textContent = item ? "Replace" : "Add to the module";
      }
    };
  }

  function sectionChange(node, t, entryOf) {
    const key = t.dataset.ccAll || t.dataset.ccSection;
    const entry = entryOf(key);
    if (t.dataset.ccAll) {
      for (const box of node.querySelectorAll(`[data-cc-section="${CSS.escape(key)}"]`)) { /** @type {HTMLInputElement} */ (box).disabled = t.checked; if (t.checked) /** @type {HTMLInputElement} */ (box).checked = false; }
      if (t.checked) entry.sections = [];
    } else {
      entry.sections = [...node.querySelectorAll(`[data-cc-section="${CSS.escape(key)}"]:checked`)].map((box) => /** @type {HTMLInputElement} */ (box).value);
    }
    touch();
  }

  // ---- pages -----------------------------------------------------------------------------------------------------------------
  // The pages and the assignments are a list beside its editor: the list scrolls in its own box and stays in view under the bar,
  // so choosing a row far down never leaves its editor off the screen.
  function pagesMarkup() {
    const pages = state.draft.pages;
    const open = state.openPage === null ? null : pages[state.openPage];
    const row = (/** @type {any} */ p, /** @type {number} */ n) => {
      const marks = `${p.front_page ? '<span class="chip" data-tone="info">Front page</span>' : ""}${p.published ? "" : '<span class="chip" data-tone="neutral">Unpublished</span>'}${p.sections?.length ? '<span class="chip" data-tone="neutral">Some sections</span>' : ""}${managedMark(p)}`;
      return `<li><button type="button" class="cc-page-link" data-cc-open-page="${n}" aria-current="${n === state.openPage}"><span class="cc-row-name">${e(p.title)}</span>${marks ? `<span class="cc-row-marks">${marks}</span>` : ""}</button></li>`;
    };
    const editor = open ? `<section class="cc-page-editor" aria-label="Edit ${e(open.title)}">
        <div class="cc-fields">
          <label class="field cc-wide">Title<input type="text" value="${e(open.title)}" maxlength="255" data-cc-page-title></label>
          <div class="cc-checks"><label class="checkbox-field"><input type="checkbox" data-cc-page-published ${open.published ? "checked" : ""}>Published</label>
            <label class="checkbox-field"><input type="checkbox" data-cc-page-front ${open.front_page ? "checked" : ""}>Front page</label></div>
          ${sectionBoxes(open, "page")}
        </div>
        ${bodyEditorMarkup(open.body, "Page")}
        <div class="cc-actions"><span class="cc-spacer"></span><button type="button" class="button danger" data-cc-page-remove>Delete page</button></div>
      </section>` : '<p class="cc-empty cc-choose">Choose a page to edit it.</p>';
    return `<div class="cc-pages"><nav class="cc-page-list" aria-label="Pages"><div class="cc-list-head"><button type="button" class="text-button" data-cc-add-page>Add page</button></div>
      <div class="cc-list-scroll" data-cc-scroll="pages"><ul>${pages.map(row).join("")}</ul></div></nav>${editor}</div>`;
  }

  function bindPages(node) {
    const pages = state.draft.pages;
    for (const b of node.querySelectorAll("[data-cc-open-page]")) /** @type {HTMLElement} */ (b).onclick = () => { state.openPage = Number(/** @type {HTMLElement} */ (b).dataset.ccOpenPage); draw(); reveal(); };
    /** @type {HTMLElement} */ (node.querySelector("[data-cc-add-page]")).onclick = () => {
      pages.push({ ref: newKey("page"), title: "New page", body: "<p></p>", published: false, front_page: false, sections: [] });
      state.openPage = pages.length - 1;
      touch();
      draw();
      reveal();
      host()?.querySelector('.cc-page-link[aria-current="true"]')?.scrollIntoView({ block: "nearest" });
    };
    if (state.openPage === null) return;
    const page = pages[state.openPage];
    const title = /** @type {HTMLInputElement} */ (node.querySelector("[data-cc-page-title]"));
    title.oninput = () => { page.title = title.value; touch(); };
    /** @type {HTMLInputElement} */ (node.querySelector("[data-cc-page-published]")).onchange = (event) => { page.published = /** @type {HTMLInputElement} */ (event.target).checked; touch(); };
    /** @type {HTMLInputElement} */ (node.querySelector("[data-cc-page-front]")).onchange = (event) => {
      const on = /** @type {HTMLInputElement} */ (event.target).checked;
      for (const other of pages) other.front_page = false;
      page.front_page = on;
      if (on) page.published = true;
      touch();
      draw();
    };
    node.querySelector(".cc-page-editor")?.addEventListener("change", (event) => {
      const t = /** @type {HTMLInputElement} */ (event.target);
      if (t.dataset.ccAll || t.dataset.ccSection) sectionChange(node, t, () => page);
    });
    /** @type {HTMLElement} */ (node.querySelector("[data-cc-page-remove]")).onclick = () => {
      const used = state.draft.modules.some((m) => m.items.some((i) => i.type === "Page" && i.page === (page.id || page.ref)));
      if (used) { toast("A module item shows this page: remove the item first."); return; }
      if (!confirm(direct() ? `Delete “${page.title}”? It is deleted in Canvas only if you tick it in the push window.` : `Delete “${page.title}” from Faculty’s copy? Canvas keeps its copies unless you tick them for deletion when you push.`)) return;
      pages.splice(state.openPage, 1);
      state.openPage = null;
      touch();
      draw();
    };
    bindBodyEditor(node, () => page.body, (html) => { page.body = html; });
  }

  /** The body editor: a sandboxed frame in design mode (no script runs; Canvas's markup is kept) with a small toolbar, or its HTML. */
  function bodyEditorMarkup(html, what) {
    return `<div class="cc-editor-bar"><div class="cc-toolbar" role="toolbar" aria-label="Formatting" ${state.html ? "hidden" : ""}>
          ${[["bold", "Bold", "B"], ["italic", "Italic", "I"], ["formatBlock:h3", "Heading", "H"], ["formatBlock:p", "Paragraph", "¶"], ["insertUnorderedList", "Bulleted list", "•"], ["insertOrderedList", "Numbered list", "1."], ["createLink", "Link", "Link"], ["unlink", "Remove link", "Unlink"]]
            .map(([command, label, text]) => `<button type="button" class="button cc-tool" data-cc-command="${command}" aria-label="${label}" title="${label}">${e(text)}</button>`).join("")}
        </div>
        <label class="checkbox-field cc-editor-switch"><input type="checkbox" data-cc-html ${state.html ? "checked" : ""}>Edit the HTML</label></div>
        ${state.html ? `<textarea class="cc-html" data-cc-body-html spellcheck="false" aria-label="${e(what)} HTML">${e(html)}</textarea>`
          : `<iframe class="cc-frame" data-cc-frame sandbox="allow-same-origin" title="${e(what)} body"></iframe>`}
        <small class="field-hint cc-editor-hint">Images and embedded videos come from Canvas and may not show here; they are kept as they are.</small>`;
  }

  function bindBodyEditor(node, read, write) {
    /** @type {HTMLInputElement} */ (node.querySelector("[data-cc-html]")).onchange = (event) => { state.html = /** @type {HTMLInputElement} */ (event.target).checked; draw(); };
    const textarea = /** @type {HTMLTextAreaElement|null} */ (node.querySelector("[data-cc-body-html]"));
    if (textarea) { textarea.oninput = () => { write(textarea.value); touch(); }; return; }
    const frame = /** @type {HTMLIFrameElement} */ (node.querySelector("[data-cc-frame]"));
    frame.srcdoc = `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="${new URL("./canvas-page-frame.css", import.meta.url).href}"></head><body>${read()}</body></html>`;
    frame.onload = () => {
      const doc = frame.contentDocument;
      if (!doc) return;
      doc.designMode = "on";
      const sync = () => { write(doc.body.innerHTML); touch(); };
      doc.addEventListener("input", sync);
      for (const b of node.querySelectorAll("[data-cc-command]")) {
        /** @type {HTMLElement} */ (b).onclick = () => {
          const [command, value] = (/** @type {HTMLElement} */ (b).dataset.ccCommand || "").split(":");
          if (command === "createLink") {
            const url = prompt("Link address (https://…)");
            if (!url) return;
            doc.execCommand("createLink", false, url);
          } else doc.execCommand(command, false, value ? `<${value}>` : undefined);
          sync();
          frame.contentWindow?.focus();
        };
      }
    };
  }

  // ---- assignments -----------------------------------------------------------------------------------------------------------
  const SUBMISSION_LABELS = { none: "No submission", on_paper: "On paper", online_upload: "File upload", online_text_entry: "Text entry", online_url: "Website address" };
  const GRADING_LABELS = { points: "Points", percent: "Percentage", letter_grade: "Letter grade", gpa_scale: "GPA scale", pass_fail: "Complete / incomplete", not_graded: "Not graded" };
  const CANVAS_KINDS = { quiz: "Canvas quiz", webassign: "WebAssign", canvas: "In Canvas" };

  /** The list beside the editor: one fold per Assignment Group, or, while the find box holds words, the rows that match. */
  function assignmentRows() {
    const list = state.draft.assignments || [];
    // A row is its name over one muted line of facts; only what needs a look is a chip (no group yet, unpublished).
    const row = (/** @type {any} */ a, /** @type {number} */ n) => {
      const facts = [`${a.points} pts`, a.due_at ? `due ${dayText(a.due_at)}` : "", a.gradebook_item ? "Gradebook column" : "", a.faculty_assignment ? "Faculty assignment" : ""].filter(Boolean).join(" · ");
      const marks = `${a.group ? "" : '<span class="chip" data-tone="warning">Choose its Assignment Group</span>'}${a.published ? "" : '<span class="chip" data-tone="neutral">Unpublished</span>'}${managedMark(a)}`;
      return `<li><button type="button" class="cc-page-link" data-cc-open-assignment="${n}" aria-current="${n === state.openAssignment}"><span class="cc-row-name">${e(a.name)}</span><small class="cc-assignment-facts">${e(facts)}</small>${marks ? `<span class="cc-row-marks">${marks}</span>` : ""}</button></li>`;
    };
    // COLUMN ASSIGNMENTS (requirement, 2026-10-06): a gradebook column whose assignment Canvas holds and the copy does not keep (a Canvas quiz, an outside tool's)
    // is listed read-only in its category's group; one without a category is named above the list.
    const others = (state.view.columns || {}).canvas_only || [];
    const other = (/** @type {any} */ c) => `<li class="cc-canvas-only" title="Canvas keeps this assignment: edit it in Canvas. Its grades come through Sync with Canvas."><span class="cc-page-static"><span class="cc-row-name">${e(c.title)}</span><small class="cc-assignment-facts">${e([`${c.points} pts`, c.due_date ? `due ${dayText(`${c.due_date}T12:00`)}` : "", `${CANVAS_KINDS[c.kind] || CANVAS_KINDS.canvas}: edited in Canvas`].filter(Boolean).join(" · "))}</small></span></li>`;
    const words = String(state.find || "").trim().toLowerCase();
    if (words) {
      const has = (/** @type {string} */ text) => String(text || "").toLowerCase().includes(words);
      const found = `${list.map((a, n) => (has(a.name) || has(a.group) ? row(a, n) : "")).join("")}${others.map((c) => (has(c.title) || has(c.group) ? other(c) : "")).join("")}`;
      return found ? `<ul class="cc-found">${found}</ul>` : '<p class="cc-empty">No assignment has those words.</p>';
    }
    // BY ASSIGNMENT GROUP (requirement, 2026-10-06): one fold per group, in Canvas's order, the copy's order inside it; any without a group first, since it must be chosen.
    // Each starts open; one the instructor closes stays closed for them (ui.js keyed disclosures, per viewer), except the group of
    // the assignment being edited.
    return assignmentGroups(list, others).map(({ group, entries, canvas }) => {
      const key = `canvas:assignment-group:${group.toLowerCase()}`;
      const open = (rememberedOpen(key) ?? true) || entries.some(({ n }) => n === state.openAssignment);
      const hint = group ? plural(entries.length + canvas.length, "assignment") : `${entries.length} to choose`;
      return `<details class="disclosure cc-group" data-cc-group="${e(group)}" data-disclosure-key="${e(key)}"${open ? " open" : ""}><summary><span>${e(group || "No Assignment Group")}</span><span class="disclosure-hint">${e(hint)}</span></summary>
        <ul>${entries.map(({ a, n }) => row(a, n)).join("")}${canvas.map(other).join("")}</ul></details>`;
    }).join("") || '<p class="cc-empty">No assignments yet.</p>';
  }

  function assignmentsMarkup() {
    const list = state.draft.assignments || [];
    const open = state.openAssignment == null ? null : list[state.openAssignment];
    const groups = [...new Set(list.map((a) => a.group).filter(Boolean))].sort();
    const ungrouped = (state.view.columns || {}).ungrouped || [];
    const needs = ungrouped.length && state.group ? `<div class="callout cc-ungrouped" data-tone="warning"><p><strong>${e(plural(ungrouped.length, "gradebook column"))} ${ungrouped.length === 1 ? "has" : "have"} no category</strong>, so no Assignment Group: ${e(ungrouped.slice(0, 3).map((c) => c.title).join(", "))}${ungrouped.length > 3 ? "…" : ""}.</p><a class="link-arrow" href="#course/${encodeURIComponent(state.group)}/home/gradebook/setup">Give ${ungrouped.length === 1 ? "it" : "them"} a category</a></div>` : "";
    const sections = state.view.sections || [];
    const editor = open ? `<section class="cc-page-editor" aria-label="Edit ${e(open.name)}">
        <div class="cc-fields">
          <label class="field cc-wide">Name<input type="text" value="${e(open.name)}" maxlength="255" data-cc-a="name"></label>
          <label class="field cc-mid">Assignment Group<input type="text" value="${e(open.group)}" list="cc-groups" maxlength="255" required placeholder="Required" data-cc-a="group"></label>
          <label class="field cc-narrow">Points<input type="number" min="0" max="100000" step="any" value="${e(open.points)}" data-cc-a="points"></label>
          <label class="field">Graded as<select data-cc-a="grading_type">${Object.entries(GRADING_LABELS).map(([k, v]) => `<option value="${k}" ${open.grading_type === k ? "selected" : ""}>${v}</option>`).join("")}</select></label>
          <label class="field">Handed in${open.external ? '<input type="text" value="Through its tool (set in Canvas)" disabled>' : `<select data-cc-a="submission">${Object.entries(SUBMISSION_LABELS).map(([k, v]) => `<option value="${k}" ${(open.submission_types || ["none"])[0] === k ? "selected" : ""}>${v}</option>`).join("")}</select>`}</label>
          <label class="field">Due<input type="datetime-local" value="${e(localTime(open.due_at))}" data-cc-a="due_at"></label>
          <label class="field">Available from<input type="datetime-local" value="${e(localTime(open.unlock_at))}" data-cc-a="unlock_at"></label>
          <label class="field">Until<input type="datetime-local" value="${e(localTime(open.lock_at))}" data-cc-a="lock_at"></label>
          <div class="cc-checks"><label class="checkbox-field"><input type="checkbox" data-cc-a="published" ${open.published ? "checked" : ""}>Published</label>
            <label class="checkbox-field"><input type="checkbox" data-cc-a="omit_from_final_grade" ${open.omit_from_final_grade ? "checked" : ""}>Not counted in Canvas’s total</label></div>
        </div>
        <datalist id="cc-groups">${groups.map((g) => `<option value="${e(g)}">`).join("")}</datalist>
        ${sections.length > 1 ? `<details class="disclosure"${Object.keys(open.section_due || {}).length ? " open" : ""}><summary><span>Due dates by section</span><span class="disclosure-hint">${Object.keys(open.section_due || {}).length || "same for all"}</span></summary><div class="disclosure-body cc-fields">${sections.map((sec) => `<label class="field">Section ${e(sec.number)}<input type="datetime-local" value="${e(localTime((open.section_due || {})[sec.id]))}" data-cc-section-due="${e(sec.id)}"></label>`).join("")}</div></details>` : ""}
        ${sectionBoxes(open, "assignment")}
        ${bodyEditorMarkup(open.description || "", "Description")}
        <div class="cc-actions"><span class="cc-spacer"></span><button type="button" class="button danger" data-cc-assignment-remove>Remove from Faculty’s copy</button></div>
      </section>` : '<p class="cc-empty cc-choose">Choose an assignment to edit it.</p>';
    return `${needs}<div class="cc-pages"><nav class="cc-page-list cc-assignment-list" aria-label="Assignments">
      <div class="cc-list-head"><input type="search" class="cc-find" data-cc-find value="${e(state.find || "")}" placeholder="Find an assignment" aria-label="Find an assignment" autocomplete="off">
        <button type="button" class="text-button" data-cc-add-assignment>Add assignment</button>${direct() ? "" : '<button type="button" class="text-button" data-cc-from-gradebook>From the gradebook…</button>'}</div>
      <div class="cc-list-scroll" data-cc-scroll="assignments" data-cc-assignment-rows>${assignmentRows()}</div></nav>${editor}</div>`;
  }

  function bindAssignments(node) {
    const list = state.draft.assignments ||= [];
    const rows = /** @type {HTMLElement} */ (node.querySelector("[data-cc-assignment-rows]"));
    // One listener on the list's box: its rows are written again as the find box changes.
    rows.onclick = (event) => {
      const b = /** @type {HTMLElement|null} */ (/** @type {HTMLElement} */ (event.target).closest("[data-cc-open-assignment]"));
      if (!b) return;
      state.openAssignment = Number(b.dataset.ccOpenAssignment);
      draw();
      reveal();
    };
    const find = /** @type {HTMLInputElement} */ (node.querySelector("[data-cc-find]"));
    find.oninput = () => { state.find = find.value; rows.innerHTML = assignmentRows(); rows.scrollTop = 0; };
    /** @type {HTMLElement} */ (node.querySelector("[data-cc-add-assignment]")).onclick = () => {
      list.push(blankAssignment({ name: "New assignment" }));
      state.openAssignment = list.length - 1;
      state.find = "";
      touch();
      draw();
      reveal();
      host()?.querySelector('.cc-page-link[aria-current="true"]')?.scrollIntoView({ block: "nearest" });
    };
    const gradebook = /** @type {HTMLElement|null} */ (node.querySelector("[data-cc-from-gradebook]"));
    if (gradebook) gradebook.onclick = () => { void fromGradebook(); };
    if (state.openAssignment == null || !list[state.openAssignment]) return;
    const a = list[state.openAssignment];
    const editor = /** @type {HTMLElement} */ (node.querySelector(".cc-page-editor"));
    editor.addEventListener("input", (event) => {
      const t = /** @type {HTMLInputElement} */ (event.target), field = t.dataset.ccA;
      if (field === "name" || field === "group" || field === "points") { a[field] = t.value; touch(); }
    });
    editor.addEventListener("change", (event) => {
      const t = /** @type {HTMLInputElement} */ (event.target), field = t.dataset.ccA;
      if (field === "grading_type") a.grading_type = t.value;
      else if (field === "submission") a.submission_types = [t.value];
      else if (field === "due_at" || field === "unlock_at" || field === "lock_at") a[field] = isoTime(t.value);
      else if (field === "published" || field === "omit_from_final_grade") a[field] = t.checked;
      else if (t.dataset.ccSectionDue) {
        a.section_due = { ...(a.section_due || {}) };
        if (t.value) a.section_due[t.dataset.ccSectionDue] = isoTime(t.value); else delete a.section_due[t.dataset.ccSectionDue];
      } else if (t.dataset.ccAll || t.dataset.ccSection) { sectionChange(node, t, () => a); return; }
      else return;
      touch();
    });
    /** @type {HTMLElement} */ (node.querySelector("[data-cc-assignment-remove]")).onclick = () => {
      const key = a.id || a.ref;
      if (state.draft.modules.some((m) => m.items.some((i) => i.content?.assignment === key))) { toast("A module item shows this assignment: remove the item first."); return; }
      if (!confirm(direct() ? `Remove “${a.name}”? Canvas deletes it, with its grades, only if you tick it in the push window.` : `Remove “${a.name}” from Faculty’s copy? Canvas keeps it, with its grades, unless you tick it for deletion when you push.`)) return;
      list.splice(state.openAssignment, 1);
      state.openAssignment = null;
      touch();
      draw();
    };
    bindBodyEditor(node, () => a.description || "", (html) => { a.description = html; });
  }

  /** The copy's assignments by Assignment Group: [{group, entries: [{a, n}], canvas: [column]}], the copy's index `n` kept, with
   * the gradebook columns Canvas holds (`others`, COLUMN ASSIGNMENTS) in the group of their category's name (case and spacing
   * aside). The group without a name ("") comes first; the others in the order they first appear in the copy, which is Canvas's
   * own order, then any only the columns name. */
  function assignmentGroups(list, others = []) {
    /** @type {Map<string, {group: string, entries: {a: any, n: number}[], canvas: any[]}>} */
    const groups = new Map();
    const folded = (/** @type {string} */ name) => name.toLowerCase().replace(/\s+/g, " ");
    const slot = (/** @type {string} */ name) => {
      const key = folded(name);
      if (!groups.has(key)) groups.set(key, { group: name, entries: [], canvas: [] });
      return /** @type {{group: string, entries: {a: any, n: number}[], canvas: any[]}} */ (groups.get(key));
    };
    list.forEach((a, n) => { slot(String(a.group || "").trim()).entries.push({ a, n }); });
    for (const column of others) slot(String(column.group || "").trim()).canvas.push(column);
    return [...groups.values()].sort((x, y) => Number(Boolean(x.group)) - Number(Boolean(y.group)));
  }

  /** The Assignments tab's count: the copy's assignments and the gradebook columns Canvas holds beside them. */
  function assignmentCount() {
    return (state.draft.assignments || []).length + ((state.view.columns || {}).canvas_only || []).length;
  }

  function blankAssignment(fields) {
    return { ref: newKey("assignment"), name: "", description: "", points: "10", grading_type: "points", submission_types: ["on_paper"], external: false,
      group: "", due_at: null, section_due: {}, unlock_at: null, lock_at: null, published: false, omit_from_final_grade: false, gradebook_item: null, faculty_assignment: null, sections: [], ...fields };
  }

  /** The gradebook's columns that no assignment of the copy stands for: ticked ones become assignments (name, points, group from
   * the column's category, due at 11:59 PM on the column's due date), each naming its column so Sync with Canvas knows where it goes. */
  async function fromGradebook() {
    const owner = state;
    if (owner.gradebookReading) return;
    const dialogContent = document.querySelector("#dialog")?.firstElementChild;
    const current = () => active(owner) && document.querySelector("#dialog")?.firstElementChild === dialogContent;
    owner.gradebookReading = true;
    let view;
    try {
      view = await api(`/api/gradebook/terms/${encodeURIComponent(owner.termId)}`);
    } catch (failure) {
      if (current()) toast(failure?.message || "The gradebook could not be read.", { tone: "danger" });
      return;
    } finally { owner.gradebookReading = false; }
    if (!current()) return;
    const taken = new Set((state.draft.assignments || []).map((a) => a.gradebook_item).filter(Boolean));
    const names = new Set((state.draft.assignments || []).map((a) => a.name.trim().toLowerCase()));
    const categories = Object.fromEntries((view.setup?.categories || []).map((c) => [c.id, c.name]));
    const columns = (view.setup?.items || []).filter((item) => !item.archived && !taken.has(item.id));
    showDialog(`<form class="cc-dialog" data-cc-from-form novalidate>${dialogHeader("Assignments from the gradebook", "Columns no assignment of Faculty’s copy stands for")}
      <div class="dialog-body">${columns.length ? `<p class="field-hint">Each ticked column becomes an assignment of the copy (unpublished, handed in on paper), linked to its column so Sync with Canvas knows where its grades go. Push to make them in Canvas.</p>
        <ul class="cc-list">${columns.map((item) => { const clash = names.has(item.title.trim().toLowerCase()); return `<li><label class="checkbox-field"><input type="checkbox" value="${e(item.id)}" ${clash ? "disabled" : ""}><span><strong>${e(item.title)}</strong> <small>${e(`${categories[item.category_id] || "Uncategorized"} · ${item.points} pts${item.due_date ? ` · due ${item.due_date}` : ""}${clash ? " · an assignment of this name is already in the copy" : ""}`)}</small></span></label></li>`; }).join("")}</ul>`
        : "<p>Every gradebook column already has an assignment in Faculty’s copy.</p>"}</div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button>${columns.length ? '<button type="submit" class="button primary">Add to the copy</button>' : ""}</footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.querySelector("[data-cc-from-form]"));
    const formCurrent = dialogSession(form, /** @type {HTMLDialogElement} */ (document.querySelector("#dialog")));
    form.onsubmit = (event) => {
      event.preventDefault();
      if (!active(owner) || !formCurrent()) return;
      const chosen = [...form.querySelectorAll("input:checked")].map((box) => /** @type {HTMLInputElement} */ (box).value);
      for (const id of chosen) {
        const item = columns.find((c) => c.id === id);
        const dueAt = item.due_date ? isoTime(`${item.due_date}T23:59`) : null;
        state.draft.assignments.push(blankAssignment({ name: item.title, points: String(item.points), group: categories[item.category_id] || "", due_at: dueAt, gradebook_item: item.id }));
      }
      document.querySelector("#dialog")?.close();
      if (chosen.length) { state.tab = "assignments"; state.openAssignment = state.draft.assignments.length - 1; state.find = ""; touch(); draw(); }
    };
  }

  // ---- saving ----------------------------------------------------------------------------------------------------------------
  async function startCopy() {
    const owner = state;
    const node = host();
    const select = /** @type {HTMLSelectElement} */ (node?.querySelector("[data-cc-source]"));
    const button = /** @type {HTMLButtonElement} */ (node?.querySelector("[data-cc-start]"));
    const error = /** @type {HTMLElement} */ (node?.querySelector(".form-error"));
    const requestId = crypto.randomUUID(), courseId = select.value;
    setBusy(button, true);
    button.textContent = "Reading Canvas…";
    try {
      const view = await canvasCall(() => api(`/api/canvas/content/${encodeURIComponent(owner.termId)}/start`, "POST",
        { course_id: courseId, request_id: requestId, base_revision: null }, {}, LONG), owner);
      if (!active(owner)) return;
      if (!view) { setBusy(button, false); button.textContent = "Start from this course"; return; }
      state.view = view;
      state.draft = clone(view.content);
      state.dirty = false;
      toast(view.skipped?.length ? `Faculty’s copy is ready. ${plural(view.skipped.length, "item")} could not be copied.` : "Faculty’s copy is ready.");
      draw();
    } catch (failure) {
      if (!active(owner)) return;
      error.hidden = false;
      error.textContent = failure?.message || "The course could not be read.";
      setBusy(button, false);
      button.textContent = "Start from this course";
    }
  }

  /** The first assignment without an Assignment Group, opened with a message, or false: required (2026-10-02), since Canvas puts an
   * assignment made without one in the course's first Assignment Group. */
  function missingGroup() {
    const list = state.draft.assignments || [];
    const n = list.findIndex((a) => !String(a.group || "").trim());
    if (n < 0) return false;
    state.tab = "assignments";
    state.openAssignment = n;
    state.find = "";
    draw();
    toast(`Choose the Assignment Group of “${list[n].name}”: Canvas puts an assignment without one in the course’s first Assignment Group.`, { tone: "danger" });
    return true;
  }

  async function saveCopy(owner = state) {
    if (!active(owner) || owner.saving || missingGroup()) return;
    owner.saving = true;
    owner.requestId ||= crypto.randomUUID();
    const editRevision = owner.editRevision || 0;
    const content = clone(owner.draft);
    // Keep object references while the request runs: edits can reorder or remove new rows before the server assigns their IDs.
    const submitted = Object.fromEntries(["modules", "pages", "assignments"].map((kind) => [kind,
      (owner.draft[kind] || []).map((entry) => ({ entry, items: [...(entry.items || [])] }))]));
    const bar = host()?.querySelector("[data-cc-savebar]");
    if (bar) bar.outerHTML = saveBar();
    try {
      const view = await api(`/api/canvas/content/${encodeURIComponent(owner.termId)}`, "POST",
        { base_revision: owner.view.revision, request_id: owner.requestId, content }, {}, 60000);
      owner.view = view;
      if ((owner.editRevision || 0) === editRevision) {
        owner.draft = clone(view.content);
        owner.dirty = false;
      } else {
        const assigned = new Map();
        for (const kind of ["modules", "pages", "assignments"]) {
          submitted[kind].forEach(({ entry, items }, index) => {
            const saved = view.content[kind]?.[index];
            if (!saved) return;
            if (!entry.id && saved.id) {
              if (entry.ref) assigned.set(entry.ref, saved.id);
              entry.id = saved.id;
              delete entry.ref;
            }
            items.forEach((item, itemIndex) => { if (!item.id && saved.items?.[itemIndex]?.id) item.id = saved.items[itemIndex].id; });
          });
        }
        for (const module of owner.draft.modules) for (const item of module.items) {
          if (assigned.has(item.page)) item.page = assigned.get(item.page);
          if (assigned.has(item.content?.assignment)) item.content.assignment = assigned.get(item.content.assignment);
        }
      }
      owner.requestId = "";
      toast("Saved.");
    } catch (failure) {
      toast(failure?.message || "The changes could not be saved.", { tone: "danger" });
    } finally {
      owner.saving = false;
      if (active(owner)) draw();
    }
  }

  const canvasCall = (run, owner) => withCanvasSignIn(run, { api, showDialog, dialogHeader, escape, current: () => active(owner) });

  // ---- the push (canvas-push.js) --------------------------------------------------------------------------------------------
  async function openPush() {
    const owner = state;
    const onResolve = (key, field, value) => {
      if (!active(owner)) throw new Error("Reopen this course to resolve its text.");
      const [kind, id] = key.split(':');
      const rows = kind === 'item' ? state.draft.modules.flatMap(m => m.items) : state.draft[`${kind}s`];
      const row = rows?.find(r => r.id === id);
      if (!row || !['body', 'description', 'title', 'name'].includes(field)) throw new Error('Open this object in the editor to resolve it.');
      row[field] = value;
      touch(); draw(); // the push window says how many texts went into the draft (one toast for several)
    };
    if (direct()) {
      if (state.dirty && missingGroup()) return;
      // One section's course: the edit itself is what is previewed and pushed, with what the page was opened on.
      const base = `/api/canvas/sections/${encodeURIComponent(state.sectionId)}/content`;
      await pushWindow.open({
        title: `Push to Section ${state.section}’s Canvas course`, subtitle: state.view.course.name, courses: [state.view.course],
        pushed: [state.view.course.course_id], previewPath: `${base}/preview`, pushPath: `${base}/push`, direct: true,
        extra: { draft: state.draft, base: state.view.base },
        onResolve,
        onStarted: (started) => { owner.view.push = started; if (active(owner)) { draw(); watch(started.id); } },
      });
      return;
    }
    if (state.dirty) { toast("Save your changes first."); return; }
    const base = `/api/canvas/content/${encodeURIComponent(state.termId)}`;
    await pushWindow.open({
      title: "Push to Canvas", subtitle: `${state.view.term.code} · ${state.view.term.term_label}`, courses: state.view.courses,
      pushed: state.view.pushed_courses || [], previewPath: `${base}/preview`, pushPath: `${base}/push`, pairPath: `${base}/pair`,
      onResolve, save: async () => { await saveCopy(owner); return active(owner) && !owner.dirty; }, // "Save and preview again": the ordinary Save
      onStarted: (started) => { owner.view.push = started; if (active(owner)) { draw(); watch(started.id); } },
    });
  }

  /** @type {{id: string, owner: any}|null} */
  let watching = null;
  function watch(pushId) {
    const owner = state;
    if (watching?.id === pushId && watching.owner === owner) return;
    const watch = watching = { id: pushId, owner };
    const current = () => watching === watch && active(owner);
    const tick = async () => {
      if (!current()) return;
      try {
        const current = await api(direct() ? `/api/canvas/sections/${encodeURIComponent(state.sectionId)}/content/pushes/${encodeURIComponent(pushId)}`
          : `/api/canvas/content/${encodeURIComponent(state.termId)}/pushes/${encodeURIComponent(pushId)}`);
        if (!active(owner) || watching !== watch) return;
        owner.view.push = current;
        refreshPush();
        if (current.status === "running") { setTimeout(() => { void tick(); }, 1500); return; }
        watching = null;
        if (direct()) {
          // The section's course as it is now: read again, so the page holds what Canvas holds.
          toast(current.status === "done" ? "The push to Canvas finished." : "The push to Canvas stopped.");
          await render(state.group, true, state.section);
          return;
        }
        // What the push wrote is now Faculty's record: read the view again (which courses have the copy, the guide line).
        try {
          const fresh = await api(`/api/canvas/content/${encodeURIComponent(state.termId)}`);
          if (!active(owner)) return;
          state.view = { ...state.view, pushed_courses: fresh.pushed_courses, push: current };
        } catch { /* the note already says what happened */ }
        if (host() && !state.dirty) draw();
        else host()?.querySelector("[data-cc-guide]")?.remove();
        toast(current.status === "done" ? "The push to Canvas finished." : "The push to Canvas stopped.");
      } catch {
        setTimeout(() => { void tick(); }, 5000);
      }
    };
    void tick();
  }

  return { render, dirty: () => Boolean(state?.dirty) };
}
