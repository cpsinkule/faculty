// @ts-check
/* Command palette (Ctrl K / ⌘K) and the '?' shortcut sheet (UX overhaul, 2026-09-26; amendment 13: small).

   One box that goes anywhere: the pages, the places visited recently (this browser only), courses, sections (their gradebook
   tab) and their rosters, assignments (once each, by the assignment shell), seating charts, rooms, gradebooks, the scans
   in the Inbox's Current list, and a few actions that open
   the SAME dialogs as their buttons. Entity lists are read lazily from existing GET endpoints the first time the palette opens
   and kept for a minute; nothing is written and nothing is decided here. The listbox follows the student picker's ARIA
   pattern (a combobox over a listbox, aria-activedescendant). No inline styles (CSP). */

import { escape as escapeText, fold } from "./ui.js";
import { routePath } from "./page-header.js";

const RECENT_KEY = "faculty:recent-places";
const RECENT_LIMIT = 8;
const SHOWN = 60;
const FRESH_MS = 60000;

/** `plan`: an assignment's grading plan, so a routed grading step opens on its tab (`routeRows`).
 * @typedef {{group: string, label: string, detail?: string, href?: string, run?: () => unknown, keywords?: string, plan?: string, key?: string}} PaletteItem */

/** This browser's localStorage, or null where site data is blocked (its getter then throws). */
function localStore() {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

/**
 * The places visited recently, newest first ([] when storage is unavailable).
 * @param {Storage | null} [storage]
 */
export function recentPlaces(storage) {
  try {
    const store = storage === undefined ? localStore() : storage;
    const value = JSON.parse(store?.getItem(RECENT_KEY) || "[]");
    return Array.isArray(value) ? value.filter((item) => item && typeof item.hash === "string" && typeof item.label === "string").slice(0, RECENT_LIMIT) : [];
  } catch {
    return [];
  }
}

/**
 * The object a route shows: one scan's stage pages, one grading plan's steps and one gradebook's pages are ONE place each, so
 * the stages of a single scan never fill the list. Any other route path is its own place.
 * @param {string} path a hash path without '#' or query
 */
export function placeKey(path) {
  const parts = path.split("/");
  if (parts[0] === "inbox" && parts[1] === "job" && parts[2]) return parts.slice(0, 3).join("/");
  if ((parts[0] === "grading" || parts[0] === "gradebook") && parts[1]) return parts.slice(0, 2).join("/");
  return path;
}

/**
 * Remember a place (newest first, one entry per object, see `placeKey`, at most eight).
 * @param {{hash: string, label: string, detail?: string}} place
 * @param {Storage | null} [storage]
 */
export function rememberPlace(place, storage) {
  if (storage === undefined) storage = localStore();
  const hash = String(place?.hash || "");
  const path = routePath(hash);
  // Only pages of one object (a course, a scan, a chart…): the list pages are always in the palette's Pages.
  if (!path.includes("/") || !place.label) return recentPlaces(storage);
  const key = placeKey(path);
  const next = [{ hash, label: String(place.label).slice(0, 160), detail: String(place.detail || "").slice(0, 160) },
    ...recentPlaces(storage).filter((item) => placeKey(routePath(item.hash)) !== key)].slice(0, RECENT_LIMIT);
  try { storage?.setItem(RECENT_KEY, JSON.stringify(next)); } catch { /* a convenience only */ }
  return next;
}

/**
 * Whether a query word is in the text. A word of digits only matches at the start of a number ("4" finds "Quiz 4" and
 * "4th", never the 4 in "MATH 140"; "50" finds "507"), so a quiz or section number does not match every course code.
 * @param {string} text folded @param {string} word folded
 */
function has(text, word) {
  if (!/^\d+$/.test(word)) return text.includes(word);
  for (let at = text.indexOf(word); at >= 0; at = text.indexOf(word, at + 1)) if (at === 0 || !/\d/.test(text[at - 1])) return true;
  return false;
}

/**
 * Items matching every word of the query (in any order, ignoring case and accents; a number from its first digit), best
 * first: a label that starts with the query, then a label containing it, then the rest. An empty query keeps the given order.
 * @param {PaletteItem[]} items @param {string} query
 */
export function matchItems(items, query) {
  const words = fold(query).split(/\s+/).filter(Boolean);
  if (!words.length) return items;
  const phrase = words.join(" ");
  return items
    .map((item, index) => {
      const label = fold(item.label);
      const haystack = `${label} ${fold(item.detail)} ${fold(item.group)} ${fold(item.keywords)}`;
      if (!words.every((word) => has(haystack, word))) return null;
      const rank = label.startsWith(phrase) ? 0 : has(label, phrase) ? 1 : words.every((word) => has(label, word)) ? 2 : 3;
      return { item, rank, index };
    })
    .filter((entry) => entry !== null)
    .map((entry) => /** @type {{item: PaletteItem, rank: number, index: number}} */ (entry))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((entry) => entry.item)
    // Each group stays together, the group holding the best match first.
    .reduce((groups, item) => {
      const group = groups.find((entry) => entry.name === item.group);
      if (group) group.items.push(item);
      else groups.push({ name: item.group, items: [item] });
      return groups;
    }, /** @type {{name: string, items: PaletteItem[]}[]} */ ([]))
    .flatMap((group) => group.items);
}

/* JEV ROUTING (docs/decisions/jev.md; requirement, 2026-10-03): a sentence typed here ("show quiz 3 flags") is matched by Jev to
   one of the palette's OWN places, so a guess opens exactly what its row would. Students, scans and recent places are never
   sent. A confident guess leads the list; a weaker one follows the word matches; a question goes to the assistant, unsent. */
const ROUTED = ["Pages", "Actions", "Courses", "Sections", "Canvas", "Assignments", "Exams", "Grading", "Seating charts", "Rooms", "Gradebooks"];
const ROUTE_WORDS = 3;
// A routed grading step's words: the assignment's tab or step that shows it (Grade, Review, Certify; 2026-10-18 one set of names).
/** @type {Record<string, string>} */
const TAB_WORDS = { jobs: "Grade", review: "Review", export: "Certify", "answer-key": "Answer key", locations: "Answer areas",
  bubble: "Bubble sheets", papers: "Papers", rubrics: "Rubrics" };

/**
 * What the route request sends, and how its ids find the palette's items again.
 * @param {PaletteItem[]} places @returns {{groups: Record<string, {id: string, label: string, detail: string}[]>, byId: Map<string, PaletteItem>}}
 */
export function routeRequest(places) {
  /** @type {Record<string, {id: string, label: string, detail: string}[]>} */
  const groups = {};
  const byId = new Map();
  for (const item of places) {
    if (!ROUTED.includes(item.group) || (!item.href && !item.run)) continue;
    // An assignment with a grading plan is sent as a grading place, so Jev names the grading step a sentence asks for ("quiz 3
    // flags"); the palette lists it once, under Assignments.
    const kind = item.group === "Assignments" && item.plan ? "Grading" : item.group;
    const list = (groups[kind] ||= []);
    if (list.length >= 2000) continue;
    const id = `${ROUTED.indexOf(kind)}:${list.length}`;
    list.push({ id, label: String(item.label).slice(0, 200), detail: String(item.detail || "").slice(0, 200) });
    byId.set(id, item);
  }
  return { groups, byId };
}

/**
 * The rows a route answer adds: {lead, rows}. `lead` rows go before the word matches.
 * @param {any} answer @param {Map<string, PaletteItem>} byId @param {string} query @param {{run: () => unknown} | undefined | false} ask
 * @returns {{lead: PaletteItem[], rows: PaletteItem[]}}
 */
export function routeRows(answer, byId, query, ask) {
  const askRow = ask ? [{ group: "Ask instead", label: `Ask the assistant: “${query}”`, detail: "Opens the assistant with this text; nothing is sent until you press Send", run: ask.run, keywords: "" }] : [];
  if (!answer) return { lead: [], rows: [] };
  if (answer.status === "not_navigation" || (answer.status === "unsure" && !(answer.picks || []).length)) return { lead: [], rows: askRow };
  if (answer.status !== "ok" && answer.status !== "unsure") return { lead: [], rows: [] };
  /** @type {PaletteItem[]} */
  const rows = (answer.picks || []).flatMap((/** @type {any} */ pick, /** @type {number} */ index) => {
    const item = byId.get(pick.id);
    if (!item) return [];
    const tab = (item.group === "Grading" || item.plan) && pick.tab && pick.tab !== "overview" && item.href ? pick.tab : "";
    const label = `${item.label}${tab ? ` · ${TAB_WORDS[tab] || tab[0].toUpperCase() + tab.slice(1)}` : ""}`;
    // A grading step's address mounts inside the assignment shell, on the tab that holds it.
    const base = item.plan ? `#grading/${encodeURIComponent(item.plan)}` : item.href;
    return [{ ...item, group: answer.confident && index === 0 ? "Best match" : "Did you mean", label, keywords: "",
      ...(tab ? { href: `${base}?tab=${encodeURIComponent(tab)}` } : {}) }];
  });
  return answer.confident && rows.length ? { lead: rows.slice(0, 1), rows: [...rows.slice(1), ...askRow] } : { lead: [], rows: [...rows, ...askRow] };
}

const termOf = (/** @type {any} */ item) => item.term === "Other" ? item.term_other : [item.term, item.year].filter(Boolean).join(" ");

/** Student results are actions, not URLs or persisted Recent entries (TEACHING-WORKFLOWS).
 * @param {any} source @param {(target: any) => unknown} openStudent @returns {PaletteItem[]} */
export function studentItems(source, openStudent) {
  return (source?.groups || []).flatMap((/** @type {any} */ group) => (group.students || []).map((/** @type {any} */ student) => ({
    group: "Students", label: student.name,
    key: `student:${group.term_id}:${student.section_id}:${student.enrollment_id}`,
    detail: `${group.code} · ${group.term_label} · Section ${(student.sections || []).join(", ")}${student.dropped ? " · Dropped" : ""}`,
    keywords: "student grades record notes absence message",
    run: () => openStudent({ termId: group.term_id, sectionId: student.section_id, enrollmentId: student.enrollment_id }),
  })));
}

/**
 * Entity items from the existing endpoints' answers (each may be missing).
 * @param {{courseGroups?: any[], courseHref?: (group: any, section?: number | null) => string, catalog?: any, grading?: any, seating?: any[], rooms?: any[], gradebook?: any, inbox?: any}} sources
 * @returns {PaletteItem[]}
 */
export function entityItems({ courseGroups = [], courseHref, catalog, grading, seating = [], rooms = [], gradebook, inbox }) {
  /** @type {PaletteItem[]} */
  const items = [];
  for (const group of courseGroups) {
    const term = termOf(group);
    const href = courseHref ? courseHref(group, null) : "#current-courses";
    items.push({ group: "Courses", label: `${group.code} · ${term}`, detail: group.name, href, keywords: group.is_current ? "current" : "" });
    items.push({ group: "Canvas", label: `Canvas content · ${group.code} · ${term}`, detail: "Modules, pages and assignments of every section",
      href: `#course/${encodeURIComponent(group.id)}/home/canvas`, keywords: "canvas modules pages assignments push" });
    for (const section of group.sections || []) {
      // A section's tab holds its gradebook, so "507 gradebook" finds it; its roster is a page of its own.
      items.push({ group: "Sections", label: `${group.code} · ${term} · Section ${section}`, detail: group.name, href: courseHref ? courseHref(group, section) : href,
        keywords: "gradebook grades scores" });
      items.push({ group: "Sections", label: `Roster · ${group.code} · ${term} · Section ${section}`, detail: group.name,
        href: `#course/${encodeURIComponent(group.id)}/section/${section}/roster`, keywords: "roster students" });
      items.push({ group: "Sections", label: `Class schedule · ${group.code} · ${term} · Section ${section}`, detail: "Class days, topics, and what is given or due",
        href: `#course/${encodeURIComponent(group.id)}/section/${section}/schedule`, keywords: "schedule dates due given exam final topics syllabus" });
      items.push({ group: "Canvas", label: `Canvas · ${group.code} · ${term} · Section ${section}`, detail: "This section’s own Canvas course, edited directly",
        href: `#course/${encodeURIComponent(group.id)}/section/${section}/canvas`, keywords: "canvas modules pages assignments section" });
    }
  }
  const definitions = new Map((catalog?.sections || []).map((/** @type {any} */ section) => [section.definition_id, section.code]));
  // ONE ENTRY PER ASSIGNMENT (2026-10-18; it was listed up to three times, under Assignments, Exams and Grading): each assignment of
  // a term opens its assignment shell, which holds its paper, exam record, grading and Certify. Current terms come from the
  // catalog's lifecycle rows, the Assignments index's own pairs; another term's grading plan by its plan's assignment and term.
  // An assignment with neither (no current term) opens Create, where it is edited.
  const titles = new Map((catalog?.assignments || []).map((/** @type {any} */ assignment) => [assignment.id, assignment]));
  const shell = (/** @type {string} */ aid, /** @type {string} */ tid) => `#assignments/${encodeURIComponent(aid)}/${encodeURIComponent(tid)}`;
  const plans = new Map((grading?.assignments || []).filter((/** @type {any} */ plan) => plan.source_assignment_id && plan.term_id)
    .map((/** @type {any} */ plan) => [`${plan.source_assignment_id}\n${plan.term_id}`, plan]));
  const listed = new Set(), placed = new Set();
  /** @param {string} aid @param {string} tid @param {string} label @param {string} detail */
  const assignmentItem = (aid, tid, label, detail) => {
    const plan = plans.get(`${aid}\n${tid}`);
    listed.add(`${aid}\n${tid}`);
    placed.add(aid);
    items.push({ group: "Assignments", label, detail, href: shell(aid, tid), keywords: "assignment exam grading print collect certify",
      ...(plan ? { plan: plan.id } : {}) });
  };
  for (const row of catalog?.lifecycle || []) {
    const assignment = titles.get(row.assignment_id);
    if (!assignment || !row.term_id) continue;
    assignmentItem(row.assignment_id, row.term_id, assignment.title,
      [assignment.code || definitions.get(assignment.definition_id) || "", row.term_label].filter(Boolean).join(" · "));
  }
  for (const plan of grading?.assignments || []) {
    if (!plan.source_assignment_id || !plan.term_id) {
      items.push({ group: "Assignments", label: plan.title, detail: [plan.code, termOf(plan)].filter(Boolean).join(" · "), href: `#grading/${encodeURIComponent(plan.id)}`, plan: plan.id });
    } else if (!listed.has(`${plan.source_assignment_id}\n${plan.term_id}`)) {
      assignmentItem(plan.source_assignment_id, plan.term_id, plan.title, [plan.code, termOf(plan)].filter(Boolean).join(" · "));
    }
  }
  for (const assignment of catalog?.assignments || []) {
    if (placed.has(assignment.id)) continue;
    items.push({ group: "Assignments", label: assignment.title, detail: `${assignment.code || definitions.get(assignment.definition_id) || ""} · Edit in Assignments`,
      href: `#documents?assignment=${encodeURIComponent(assignment.id)}&step=details` });
  }
  for (const chart of seating) {
    items.push({ group: "Seating charts", label: `${chart.title} · ${chart.room_name}`, detail: `${chart.code} · ${chart.term_label} · Section${(chart.sections || []).length === 1 ? "" : "s"} ${(chart.sections || []).join(", ")}`, href: `#seating/${encodeURIComponent(chart.id)}` });
  }
  for (const room of rooms) {
    items.push({ group: "Rooms", label: room.name, detail: "Room", href: `#rooms/${encodeURIComponent(room.id)}` });
  }
  for (const term of gradebook?.terms || []) {
    items.push({ group: "Gradebooks", label: `${term.code} · ${term.term_label || "Term not set"}`, detail: `${term.name || ""} · Gradebook`, href: `#gradebook/${encodeURIComponent(term.id)}`,
      keywords: "setup categories weights" });
  }
  for (const job of inbox?.jobs || []) {
    if (!job.scan) continue;
    items.push({ group: "Scans", label: job.scan.headline || "Scan", detail: `Teaching Inbox · ${(job.scan.facts || []).join(" · ")}`, href: `#inbox/job/${encodeURIComponent(job.id)}` });
  }
  return items;
}

/**
 * The palette and the shortcut sheet.
 * @param {{doc?: Document, api: (path: string, method?: string, data?: unknown) => Promise<any>, icon: (name: string) => string, pages: () => PaletteItem[], actions: () => PaletteItem[],
 *   openStudent?: (target: any) => unknown,
 *   courses: () => {courseGroups: any[], courseHref: (group: any, section?: number | null) => string, rooms?: any[], seating?: any[]}, shortcuts: () => {key: string, label: string}[]}} options
 */
export function createPalette({ doc = globalThis.document, api, icon, pages, actions, courses, shortcuts, openStudent }) {
  /** @type {Record<string, {at: number, value: any, pending: Promise<any> | null}>} */
  const cache = {};
  /** @type {HTMLDialogElement | null} */ let dialog = null;
  /** @type {HTMLDialogElement | null} */ let sheet = null;
  /** @type {PaletteItem[]} */ let shown = [];
  let active = -1;
  /** JEV ROUTING: answers by typed sentence (this palette's lifetime), the one being asked, its timer. */
  /** @type {Map<string, {answer: any, byId: Map<string, PaletteItem>}>} */ const routed = new Map();
  let asking = "";
  /** @type {ReturnType<typeof setTimeout> | undefined} */ let routeTimer;
  let opener = /** @type {Element | null} */ (null);

  /** One GET, kept for a minute; a failure leaves that group out. */
  /** @param {string} key @param {string} path @returns {Promise<any>} */
  function read(key, path) {
    const entry = cache[key];
    if (entry && (entry.pending || Date.now() - entry.at < FRESH_MS)) return entry.pending || Promise.resolve(entry.value);
    const pending = api(path).then((value) => { cache[key] = { at: Date.now(), value, pending: null }; return value; })
      .catch(() => { delete cache[key]; return null; });
    cache[key] = { at: 0, value: entry?.value ?? null, pending };
    return pending;
  }
  const value = (/** @type {string} */ key) => cache[key]?.value ?? null;

  function load() {
    const known = courses();
    const reads = [
      read("catalog", "/api/documents/catalog"),
      read("grading", "/api/grading?view=navigation&health=0"),
      read("gradebook", "/api/gradebook"),
      read("inbox", "/api/inbox/jobs?status=current&files=input"),
    ];
    if (!known.rooms?.length) reads.push(read("rooms", "/api/state?include=rooms"));
    if (openStudent) reads.push(read("students", "/api/student-notes/students"));
    if (!known.seating?.length) reads.push(read("seating", "/api/state?include=seating"));
    reads.forEach((promise) => promise.then(() => { if (dialog?.open) paint(false); }));
  }

  /** @param {string} query @returns {PaletteItem[]} */
  function items(query) {
    const known = courses();
    const recent = recentPlaces().map((place) => ({ group: "Recent", label: place.label, detail: place.detail, href: place.hash }));
    const entities = entityItems({
      courseGroups: known.courseGroups, courseHref: known.courseHref, catalog: value("catalog"), grading: value("grading"),
      seating: known.seating?.length ? known.seating : value("seating")?.seating_charts || [],
      rooms: known.rooms?.length ? known.rooms : value("rooms")?.rooms || [],
      gradebook: value("gradebook"), inbox: value("inbox"),
    });
    // Nothing typed: where the instructor was, the pages and the actions. Typing searches everything.
    if (!query.trim()) return [...recent, ...pages(), ...actions()];
    // A place visited recently is listed once, under Recent, not again under its own group.
    const visited = new Set(recent.map((place) => place.href));
    const matched = matchItems([...pages(), ...actions(), ...recent, ...entities.filter((item) => !visited.has(item.href)),
      ...(openStudent ? studentItems(value("students"), openStudent) : [])], query);
    const guess = routed.get(query.trim());
    if (!guess) return matched;
    const { lead, rows } = routeRows(guess.answer, guess.byId, query.trim(), actions().find((item) => item.label === "Ask the assistant") && {
      run: () => askAssistant(query.trim()) });
    return [...lead, ...matched, ...rows];
  }

  /** JEV ROUTING: after a pause in typing, a sentence of three words or more is sent with the palette's own places. */
  function scheduleRoute() {
    clearTimeout(routeTimer);
    const input = /** @type {HTMLInputElement | null} */ (dialog?.querySelector("[data-palette-input]") ?? null);
    const query = input?.value.trim() || "";
    if (query.split(/\s+/).length < ROUTE_WORDS || routed.has(query) || query.length > 300) return;
    routeTimer = setTimeout(() => {
      if (!dialog?.open || input?.value.trim() !== query) return;
      const known = courses();
      const places = [...pages(), ...actions(), ...entityItems({
        courseGroups: known.courseGroups, courseHref: known.courseHref, catalog: value("catalog"), grading: value("grading"),
        seating: known.seating?.length ? known.seating : value("seating")?.seating_charts || [],
        rooms: known.rooms?.length ? known.rooms : value("rooms")?.rooms || [], gradebook: value("gradebook")})];
      const { groups, byId } = routeRequest(places);
      if (!Object.keys(groups).length) return;
      asking = query;
      status();
      api("/api/jev/route", "POST", { query, groups })
        .then((answer) => { routed.set(query, { answer, byId }); })
        .catch(() => { routed.set(query, { answer: null, byId }); })
        .finally(() => {
          while (routed.size > 100) {
            const oldest = routed.keys().next().value;
            if (oldest === undefined) break;
            routed.delete(oldest);
          }
          if (asking === query) asking = "";
          // Only the sentence still in the box repaints the list.
          if (dialog?.open && input?.value.trim() === query) paint(false);
          else status();
        });
    }, 400);
  }

  /** Opens the assistant with the typed text in its box, never sent (the instructor presses Send). */
  function askAssistant(/** @type {string} */ text) {
    actions().find((item) => item.label === "Ask the assistant")?.run?.();
    let tries = 0;
    const fill = () => {
      const box = /** @type {HTMLTextAreaElement | null} */ (doc.getElementById("assistant-text"));
      if (box && !box.closest("[hidden]")) {
        if (!box.value.trim()) { box.value = text; box.dispatchEvent(new Event("input", { bubbles: true })); }
        box.focus(); return;
      }
      if (++tries < 20) setTimeout(fill, 50);
    };
    fill();
  }

  function status() {
    const node = dialog?.querySelector("[data-palette-status]");
    if (node && asking) node.textContent = "Understanding…";
  }

  function build() {
    dialog = doc.createElement("dialog");
    dialog.className = "palette";
    dialog.setAttribute("aria-label", "Go to");
    dialog.innerHTML = `<div class="palette-box"><div class="palette-field">${icon("search")}<input type="text" role="combobox" aria-label="Go to a page, course, assignment or action" aria-autocomplete="list" aria-expanded="true" aria-controls="palette-list" autocomplete="off" spellcheck="false" placeholder="Find a student, page, course or assignment…" data-palette-input><kbd>Esc</kbd></div>
      <ul id="palette-list" class="palette-list" role="listbox" aria-label="Results"></ul>
      <p class="palette-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>?</kbd> shortcuts</span><span class="palette-status" role="status" data-palette-status></span></p></div>`;
    doc.body.append(dialog);
    const input = /** @type {HTMLInputElement} */ (dialog.querySelector("[data-palette-input]"));
    input.addEventListener("input", () => { paint(); scheduleRoute(); });
    input.addEventListener("keydown", (event) => {
      if (event.isComposing) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); move(active + (event.key === "ArrowDown" ? 1 : -1)); }
      else if (event.key === "PageDown" || event.key === "PageUp") { event.preventDefault(); move(active + (event.key === "PageDown" ? 8 : -8)); }
      else if (event.key === "Enter") { event.preventDefault(); choose(shown[active]); }
      else if (event.key === "?" && !input.value) { event.preventDefault(); close(); openSheet(); }
    });
    const list = /** @type {HTMLElement} */ (dialog.querySelector(".palette-list"));
    list.addEventListener("mousedown", (event) => event.preventDefault());
    list.addEventListener("click", (event) => {
      const option = /** @type {HTMLElement} */ (event.target).closest("[role=option]");
      if (option) choose(shown[Number(option.getAttribute("data-index"))]);
    });
    list.addEventListener("mousemove", (event) => {
      const option = /** @type {HTMLElement} */ (event.target).closest("[role=option]");
      if (option && Number(option.getAttribute("data-index")) !== active) move(Number(option.getAttribute("data-index")), false);
    });
    // A click on the backdrop closes it.
    dialog.addEventListener("click", (event) => { if (event.target === dialog) close(); });
    dialog.addEventListener("close", () => {
      clearTimeout(routeTimer); asking = "";
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus({ preventScroll: true });
    });
    // The page underneath changed (Back, a link elsewhere): the list no longer answers what is on screen.
    doc.defaultView?.addEventListener("hashchange", () => close());
  }

  function paint(fresh = true) {
    if (!dialog) return;
    const input = /** @type {HTMLInputElement} */ (dialog.querySelector("[data-palette-input]"));
    const list = /** @type {HTMLElement} */ (dialog.querySelector(".palette-list"));
    const key = (/** @type {PaletteItem} */ item) => item.key || item.href || JSON.stringify([item.group, item.label, item.detail]);
    const selected = shown[active] ? key(shown[active]) : null;
    const all = items(input.value);
    shown = all.slice(0, SHOWN);
    let group = "", html = "";
    shown.forEach((item, index) => {
      if (item.group !== group) html += `<li role="presentation" class="palette-group">${escapeText(item.group)}</li>`;
      group = item.group;
      html += `<li role="option" id="palette-option-${index}" data-index="${index}" aria-selected="false"><span class="palette-label">${escapeText(item.label)}</span>${item.detail ? `<small>${escapeText(item.detail)}</small>` : ""}</li>`;
    });
    const loading = Object.values(cache).some((entry) => entry.pending);
    if (!shown.length) html = `<li role="presentation" class="palette-empty">${loading ? "Looking…" : `Nothing matches “${escapeText(input.value)}”.`}</li>`;
    else if (all.length > SHOWN) html += `<li role="presentation" class="palette-empty">${(all.length - SHOWN).toLocaleString()} more: keep typing to narrow the list.</li>`;
    list.innerHTML = html;
    const count = dialog.querySelector("[data-palette-status]");
    if (count) count.textContent = shown.length ? `${all.length.toLocaleString()} result${all.length === 1 ? "" : "s"}` : "";
    status();
    // A guess arriving keeps the row the instructor moved to, unless it leads the list.
    const kept = selected === null ? -1 : shown.findIndex((item) => key(item) === selected);
    move(fresh || shown[0]?.group === "Best match" ? 0 : kept >= 0 ? kept : Math.min(Math.max(active, 0), shown.length - 1));
  }

  /** @param {number} index @param {boolean} [scroll] */
  function move(index, scroll = true) {
    if (!dialog) return;
    const input = /** @type {HTMLInputElement} */ (dialog.querySelector("[data-palette-input]"));
    active = shown.length ? ((index % shown.length) + shown.length) % shown.length : -1;
    dialog.querySelectorAll("[role=option]").forEach((node) => {
      const selected = Number(node.getAttribute("data-index")) === active;
      node.setAttribute("aria-selected", String(selected));
      node.classList.toggle("active", selected);
    });
    const current = active >= 0 ? dialog.querySelector(`#palette-option-${active}`) : null;
    if (current) {
      input.setAttribute("aria-activedescendant", current.id);
      if (scroll) current.scrollIntoView({ block: "nearest" });
    } else input.removeAttribute("aria-activedescendant");
  }

  /** @param {PaletteItem | undefined} item */
  function choose(item) {
    if (!item) return;
    close();
    if (item.run) { item.run(); return; }
    if (item.href && item.href !== location.hash) location.hash = item.href;
  }

  function open() {
    if (!dialog) build();
    if (!dialog || dialog.open) return;
    if (sheet?.open) sheet.close();
    opener = doc.activeElement;
    const input = /** @type {HTMLInputElement} */ (dialog.querySelector("[data-palette-input]"));
    input.value = "";
    load();
    paint();
    dialog.showModal();
    input.focus();
  }

  function close() {
    clearTimeout(routeTimer);
    if (dialog?.open) dialog.close();
  }

  function openSheet() {
    if (!sheet) {
      sheet = doc.createElement("dialog");
      sheet.className = "palette shortcut-sheet";
      sheet.setAttribute("aria-labelledby", "shortcut-sheet-title");
      doc.body.append(sheet);
      sheet.addEventListener("click", (event) => {
        if (event.target === sheet || /** @type {HTMLElement} */ (event.target).closest("[data-sheet-close]")) sheet?.close();
      });
      sheet.addEventListener("close", () => { if (opener instanceof HTMLElement && opener.isConnected) opener.focus({ preventScroll: true }); });
    }
    if (sheet.open) return;
    opener = doc.activeElement;
    const keys = (/** @type {string} */ descriptor) => descriptor.replace(/^Mod\+/, navigator.platform?.startsWith("Mac") ? "⌘ " : "Ctrl ").split(/\s+|\+(?=.)/).map((/** @type {string} */ part) => `<kbd>${escapeText(part)}</kbd>`).join(" ");
    const rows = [...shortcuts(), { key: "Esc", label: "Close a dialog or this list" }];
    sheet.innerHTML = `<div class="palette-box"><header class="shortcut-head"><h2 id="shortcut-sheet-title">Keyboard shortcuts</h2><button type="button" class="icon-button" data-sheet-close aria-label="Close">${icon("close")}</button></header>
      <p class="shortcut-note">Shortcuts never act while you type in a field.</p>
      <dl class="shortcut-list">${rows.map((row) => `<div><dt>${keys(row.key)}</dt><dd>${escapeText(row.label)}</dd></div>`).join("")}</dl></div>`;
    sheet.showModal();
    /** @type {HTMLElement | null} */ (sheet.querySelector("[data-sheet-close]"))?.focus();
  }

  return { open, close, toggle: () => (dialog?.open ? close() : open()), openSheet, isOpen: () => Boolean(dialog?.open) };
}
