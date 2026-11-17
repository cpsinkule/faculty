/**
 * A course's question bank (AGENTS.md "Question bank"): `#course/<group>/home/questions` renders into [data-question-bank-host].
 *
 * The server keeps the bank (questions retained from approved assessments and drafted quizzes, written here or read out of a PDF), its
 * outline (chapters, sections and topics from the lecture notes, learning objectives below them) and each question's tags, which the
 * model applies automatically. This page lists them: the outline on the left filters the questions on the right (an entry shows the
 * questions under it and under every entry below it), with search, difficulty, type, source and status filters in the browser. A question
 * opens in a dialog with its parts, choices, pictures, answers, solution and history; its content, tags and pictures (an image, or a box
 * cut from a page of a PDF read for the bank) can be changed there. While the model is
 * tagging or reading a PDF the page asks again every few seconds and redraws only when something changed.
 */
import { actionMenuMarkup, bindActionMenu, calloutMarkup, disclosureMarkup, emptyMarkup, loadingMarkup, captureDialogIntent, dialogSession, plural } from "./ui.js";
import { mathText, renderMath } from "./grading-math.js";
import { viewLink } from "./file-viewer.js";

const POLL = 5000;
const PAGE = 40;
const LONG = 180000; // an upload is kept before the answer comes back
const ADD_AT_ONCE = 2000; // questions added in one request (service.MAX_ACCEPT)
const LEVELS = { chapter: "Chapter", section: "Section", topic: "Topic", objective: "Learning objective" };
const CHILD = { chapter: ["section"], section: ["topic", "objective"], topic: ["objective"], objective: [] };
const DIFFICULTY = { easy: "Easy", medium: "Medium", hard: "Hard" };
const SOURCES = { assessment: "Assessments", quiz_draft: "Drafted quizzes", instructor: "Written here", pdf: "Read from PDFs" };
const STATUS = { "": "In the bank", check: "Something to check", untagged: "Not tagged yet", model: "Tagged by the model", instructor: "Tagged by you", retired: "Retired" };
/** The "Check: …" lines a PDF's reading left in a question's notes (what the instructor should look at before using it). */
const CHECKS = /^Check:[ \t]*(.*)$/gm;
const MEASURED = { "": "Any", low: "Below 50% earned", middle: "50–79% earned", high: "80% or more earned", none: "Not measured yet" };
const ORDER = { "": "As kept", hardest: "Hardest first, by grades", easiest: "Easiest first, by grades" };
const LETTERS = "ABCDEFGH".split("");
const PICTURE_MB = 10; // question_bank/pictures.py MAX_PICTURE

/** @param {{api: any, escape: (v: any) => string, icon: (name: string) => string, toast: any, showDialog: (html: string) => void,
 *  dialogHeader: (t: string, s?: string) => string, header?: any}} deps */
export function createQuestionBank({ api, escape, icon, toast, showDialog: paintDialog, dialogHeader, header = null }) {
  const e = escape;
  const h = { e };
  /** @type {any} */ let state = null; // {groupId, definitionId, code, termLabel, view, signature, filters, shown, open, retiredEntries}
  let generation = 0;
  let timer = 0;
  // The bank's read under way (about 780 KB on the live course): the course page renders twice per navigation, and an unforced
  // render of the same bank joins it (FRONTEND.md "One read per navigation").
  /** @type {{definitionId: string, promise: Promise<void>} | null} */
  let loading = null;
  const host = () => /** @type {HTMLElement|null} */ (document.querySelector("[data-question-bank-host]"));
  const dialog = () => /** @type {HTMLDialogElement} */ (document.querySelector("#dialog"));
  const forms = new WeakMap();
  const showDialog = (markup) => {
    paintDialog(markup);
    const form = dialog().firstElementChild;
    forms.set(form, { owner: state, active: dialogSession(form, dialog()) });
  };
  const newId = (/** @type {string} */ prefix) => `${prefix}-${crypto.randomUUID()}`;
  const day = (/** @type {string} */ iso) => {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  };
  const courseHref = (/** @type {any} */ group) => `#course/${encodeURIComponent(group.id)}`;
  const route = (/** @type {string} */ path) => `/api/question-bank${path}`;
  const message = (/** @type {any} */ cause) => cause?.message || "Something went wrong.";

  // -- What the page knows ----------------------------------------------------------------------------------------------------
  function nodes() { return state?.view?.outline || []; }
  function nodeMap() { return state?.nodeIndex || new Map(); }
  function typeLabel(/** @type {string} */ key) {
    return state.view.vocabulary.types.find((/** @type {any} */ t) => t.key === key)?.label || key;
  }
  /** The entry and every entry below it. */
  function below(/** @type {string} */ id) {
    const found = new Set([id]);
    let grew = true;
    while (grew) {
      grew = false;
      for (const node of nodes()) if (node.parent_id && found.has(node.parent_id) && !found.has(node.id)) { found.add(node.id); grew = true; }
    }
    return found;
  }
  /** The entries from the top of the outline down to this one. */
  function path(/** @type {string} */ id) {
    const cached = state.paths.get(id);
    if (cached) return cached;
    const map = nodeMap();
    const chain = [];
    for (let node = map.get(id); node; node = map.get(node.parent_id)) chain.unshift(node);
    state.paths.set(id, chain);
    return chain;
  }
  function nodeName(/** @type {any} */ node, { long = false } = {}) {
    if (!node) return "";
    if (node.level === "chapter") return node.number ? `Chapter ${node.number}${long ? ` · ${node.title}` : ""}` : node.title;
    if (node.level === "section") return [node.number, node.title].filter(Boolean).join(" ");
    return node.title;
  }
  /** A tag as a chip: its section's number before a topic, an objective in its own style. */
  function tagChip(/** @type {string} */ id) {
    const chain = path(id);
    const node = chain[chain.length - 1];
    if (!node) return "";
    const section = chain.find((n) => n.level === "section");
    const text = node.level === "topic" && section?.number ? `${section.number} · ${node.title}` : nodeName(node);
    return `<button type="button" class="chip bank-tag" data-level="${e(node.level)}" data-bank-node="${e(node.id)}" title="${e(chain.map((n) => nodeName(n)).join(" › "))}">${node.level === "objective" ? `<span class="bank-tag-kind">Objective</span>` : ""}${e(text)}</button>`;
  }

  // -- The header and the page --------------------------------------------------------------------------------------------------
  function setHeader(/** @type {any} */ group, /** @type {string} */ termLabel) {
    const counts = state?.view?.counts;
    header?.set({
      route: location.hash,
      title: "Question bank",
      context: [group.code, "every term"].filter(Boolean).join(" · "),
      subtitle: counts ? `${plural(counts.questions, "question")}${counts.tagged === counts.questions ? ", all tagged" : ` · ${counts.tagged} tagged`}` : "Questions kept for every term of the course.",
      crumbs: [
        { label: "Home", href: "#overview" },
        { label: "Courses", href: "#current-courses" },
        { label: [group.code, termLabel].filter(Boolean).join(" · "), href: courseHref(group) },
        { label: "Question bank" },
      ],
      // COMPACT ROWS: the instructor comes to find, compare and check questions, not to add them, so adding is no primary button.
      action: null,
      secondary: state?.view ? [{ label: "Add a question", icon: "plus", run: () => openEdit(null) }, { label: "Read questions from a PDF", icon: "paper", run: () => openUpload() }] : [],
    });
  }

  /**
   * Mount for a course group (the course page calls this after drawing the host).
   * @param {any} group the course group ({id, definition_id, code}) @param {string} [termLabel] @param {boolean} [force]
   */
  async function render(group, termLabel = "", force = false) {
    const node = host();
    if (!node || !group) return;
    if (!group.definition_id) {
      header?.set({ route: location.hash, title: "Question bank", context: group.code || "", subtitle: "", action: null, secondary: [] });
      node.innerHTML = emptyMarkup({ icon: "book", title: "This course has no course code yet", body: "A question bank belongs to a course code. Add one in the course settings first." });
      return;
    }
    const same = state?.definitionId === group.definition_id;
    if (!same) {
      state = { groupId: group.id, definitionId: group.definition_id, code: group.code, termLabel, view: null, signature: "",
                filters: { node: "", query: "", difficulty: "", type: "", source: "", status: "", measured: "" }, shown: PAGE, open: null, retiredEntries: false,
                order: "", grouped: true, pick: {}, stats: null, moreFilters: false };
    }
    state.termLabel = termLabel;
    setHeader(group, termLabel);
    if (same && state.view && !force && node.firstElementChild && node.dataset.bankFor === group.definition_id) { schedule(); return; }
    if (!force && loading?.definitionId === group.definition_id) return loading.promise;
    const run = ++generation;
    if (!state.view) node.innerHTML = loadingMarkup("Opening the question bank…", 5);
    const promise = (async () => {
      try {
        const view = await api(route(`/definitions/${encodeURIComponent(group.definition_id)}`));
        if (run !== generation) return;
        accept(view);
        node.dataset.bankFor = group.definition_id;
        setHeader(group, termLabel);
        draw();
        void loadStats();
      } catch (cause) {
        if (run !== generation) return;
        node.innerHTML = `<div class="empty-state">${icon("info")}<h3>The question bank could not be opened</h3><p role="alert">${e(message(cause))}</p><button type="button" class="button" data-bank-retry-open>Try again</button></div>`;
        const retry = /** @type {HTMLButtonElement|null} */ (node.querySelector("[data-bank-retry-open]"));
        if (retry) retry.onclick = () => { void render(group, termLabel, true); };
      }
    })();
    loading = { definitionId: group.definition_id, promise };
    try { await promise; } finally { if (loading?.promise === promise) loading = null; }
  }

  /** How students did on each question (certified grades and bubble sheets): read after the page is drawn, never on a poll. */
  async function loadStats() {
    const definition = state?.definitionId;
    try {
      const stats = await api(route(`/definitions/${encodeURIComponent(definition)}/statistics`));
      if (state?.definitionId !== definition) return;
      state.stats = stats;
      // Only the Measured filter and the hardest/easiest order depend on them: otherwise each card drawn gets its '% earned' chip
      // in place, and the list (and its math) is not typeset a second time.
      if (state.filters.measured || state.order) drawList();
      else addMeasured();
    } catch { /* the bank works without them */ }
  }

  /** Each row on screen gets its facts line again, now with its '% earned' (plain text: no math in it). */
  function addMeasured() {
    const list = host()?.querySelector("[data-bank-list]");
    if (!list || !state?.view) return;
    const byId = new Map(state.view.questions.map((/** @type {any} */ q) => [q.id, q]));
    for (const card of list.querySelectorAll(".bank-card[data-question]")) {
      const q = byId.get(/** @type {HTMLElement} */ (card).dataset.question);
      const facts = card.querySelector(".bank-facts");
      if (q && facts && measured(q)) facts.innerHTML = rowFactsMarkup(q);
    }
  }
  const measured = (/** @type {any} */ q) => state?.stats?.questions?.[q.id] || null;

  function accept(/** @type {any} */ view) {
    state.view = view;
    state.nodeIndex = new Map(view.outline.map((node) => [node.id, node]));
    state.paths = new Map();
    state.signature = JSON.stringify(view);
    if (!state.open) state.open = new Set(view.outline.filter((/** @type {any} */ n) => n.level === "chapter").map((/** @type {any} */ n) => n.id));
    if (state.filters.node && !view.outline.some((/** @type {any} */ n) => n.id === state.filters.node)) state.filters.node = "";
  }

  /** The model is working, or will be within a turn of the worker: a run waits or runs, or a question waits to be tagged. */
  function busy() {
    const view = state?.view;
    if (!view) return false;
    const waiting = view.model.ready && view.questions.some((/** @type {any} */ q) => !q.retired && !q.classification && !q.tagging);
    return Boolean(waiting || view.counts.tagging || view.runs.some((/** @type {any} */ r) => r.status === "pending" || r.status === "running"));
  }

  /** While the model works, ask again every few seconds; redraw only when the answer changed. */
  function schedule() {
    window.clearTimeout(timer);
    if (!busy()) return;
    const definition = state.definitionId;
    timer = window.setTimeout(async () => {
      const node = host();
      if (!node || node.dataset.bankFor !== definition || state?.definitionId !== definition) return;
      try {
        const view = await api(route(`/definitions/${encodeURIComponent(definition)}`));
        if (state?.definitionId !== definition || !host()) return;
        if (JSON.stringify(view) !== state.signature) {
          const wasBusy = busy();
          accept(view);
          draw();
          refreshHeader();
          if (wasBusy && !busy()) toast("The model finished; the bank is up to date.");
        }
      } catch { /* the next look tries again */ }
      schedule();
    }, POLL);
  }

  function refreshHeader() {
    setHeader({ id: state.groupId, definition_id: state.definitionId, code: state.code }, state.termLabel);
  }

  function notices() {
    const view = state.view;
    const parts = [];
    if (!view.model.ready) {
      parts.push(calloutMarkup({ tone: "warning", title: "Questions are not tagged until a model is chosen", body: `${view.model.problem} Tagging and reading PDFs use the Rubric drafting and review model.` }));
    }
    if (view.counts.failed) {
      parts.push(calloutMarkup({ tone: "danger", title: `${plural(view.counts.failed, "question")} could not be tagged`, body: "The model's answer was refused or did not arrive. Nothing is sent again by itself.",
                                 action: { label: "Tag them again", data: { bankTagFailed: "" } } }));
    }
    if (view.counts.tagging) {
      parts.push(calloutMarkup({ tone: "info", title: `Tagging ${plural(view.counts.tagging, "question")}…`, body: "The model places each one in the outline and gives its difficulty and type. This page updates by itself." }));
    }
    if (view.counts.waiting_for_text) {
      parts.push(calloutMarkup({ tone: "info", title: `${plural(view.counts.waiting_for_text, "approved question")} without a statement`, body: "Their rubric was approved without the problem's text. They join the bank once the problem is saved in Grading." }));
    }
    if (view.counts.bubbles_unread) {
      parts.push(calloutMarkup({ tone: "warning", title: `${plural(view.counts.bubbles_unread, "graded bubble-sheet question")} not found in its blank`,
                                 body: "The bank read the version's blank from its grading template but did not find these problems there. Read the blank yourself under Read questions from a PDF, choosing its assessment and version." }));
    }
    if (!view.outline.length) {
      parts.push(calloutMarkup({ tone: "info", title: "The outline starts from the lecture notes", body: "Upload the course's lecture notes and their chapters, sections and topics appear here within a minute. You can also add entries yourself.",
                                 action: { label: "Lecture notes", href: `#course/${encodeURIComponent(state.groupId)}/home/notes` } }));
    }
    return parts.join("");
  }

  // -- The outline --------------------------------------------------------------------------------------------------------------
  function outlineMarkup() {
    const view = state.view;
    const shown = view.outline.filter((/** @type {any} */ n) => state.retiredEntries || !path(n.id).some((p) => p.retired));
    const children = new Map();
    for (const node of shown) {
      const list = children.get(node.parent_id || "") || [];
      list.push(node);
      children.set(node.parent_id || "", list);
    }
    const item = (/** @type {any} */ node) => {
      const kids = children.get(node.id) || [];
      const open = state.open.has(node.id);
      const toggle = kids.length ? `<button type="button" class="icon-button bank-toggle" data-bank-toggle="${e(node.id)}" aria-expanded="${open}" aria-label="${open ? "Hide" : "Show"} what is under ${e(nodeName(node))}">${open ? "▾" : "▸"}</button>` : `<span class="bank-toggle-space" aria-hidden="true"></span>`;
      const menu = actionMenuMarkup({ id: `bank-node-${node.id}`, label: `Change ${nodeName(node)}`, items: [
        { label: "Rename…", data: { bankRename: node.id } },
        ...CHILD[/** @type {"chapter"} */ (node.level)].map((level) => ({ label: `Add a ${LEVELS[/** @type {"chapter"} */ (level)].toLowerCase()} under it…`, data: { bankAddNode: level, bankParent: node.id }, disabled: node.retired })),
        node.retired ? { label: "Restore", data: { bankRetireNode: node.id, bankRetired: "false" } } : { label: "Retire", danger: true, data: { bankRetireNode: node.id, bankRetired: "true" } },
      ] });
      return `<li class="bank-tree-item" data-level="${e(node.level)}"${node.retired ? ' data-retired="true"' : ""}>
        <div class="bank-node-row">${toggle}<button type="button" class="bank-node" data-bank-node="${e(node.id)}"${state.filters.node === node.id ? ' aria-current="true"' : ""}>${node.level === "objective" ? `<span class="bank-node-kind">Objective</span>` : ""}<span class="bank-node-name">${e(nodeName(node, { long: true }))}</span>${node.retired ? '<span class="chip" data-tone="neutral">Retired</span>' : ""}<b class="bank-node-count">${node.questions}</b></button>${menu}</div>
        ${kids.length && open ? `<ul class="bank-tree">${kids.map(item).join("")}</ul>` : ""}</li>`;
    };
    const source = view.outline_source ? `<p class="bank-outline-source">From ${e(view.outline_source.title)} · ${e(day(view.outline_source.imported_at))}</p>` : "";
    const retired = view.outline.some((/** @type {any} */ n) => n.retired)
      ? `<label class="bank-outline-retired"><input type="checkbox" data-bank-retired-entries${state.retiredEntries ? " checked" : ""}> Show retired entries</label>` : "";
    return `<nav class="bank-outline" aria-label="Outline">
      <div class="bank-outline-head"><h3>Outline</h3><button type="button" class="text-button" data-bank-add-node="chapter">${icon("plus")}Chapter</button></div>${source}
      <button type="button" class="bank-node bank-node-all" data-bank-node=""${state.filters.node ? "" : ' aria-current="true"'}><span class="bank-node-name">All questions</span><b class="bank-node-count">${view.counts.questions}</b></button>
      ${view.outline.length ? `<ul class="bank-tree bank-tree-root">${(children.get("") || []).map(item).join("")}</ul>` : `<p class="bank-muted">No outline yet.</p>`}${retired}</nav>`;
  }

  // -- The questions ------------------------------------------------------------------------------------------------------------
  function words(/** @type {any} */ q) {
    const c = q.content;
    return [q.source_label, c.number, c.statement, ...c.parts.map((/** @type {any} */ p) => p.text), ...c.choices, ...(q.classification?.nodes || []).map((/** @type {string} */ id) => nodeName(nodeMap().get(id), { long: true })),
            ...(q.terms || [])].join(" ").toLocaleLowerCase();
  }

  function matching() {
    const f = state.filters;
    const within = f.node ? below(f.node) : null;
    const query = f.query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
    return state.view.questions.filter((/** @type {any} */ q) => {
      if (f.status === "retired" ? !q.retired : q.retired) return false;
      if (f.status === "untagged" && q.classification) return false;
      if (f.status === "check" && !checksOf(q).length) return false;
      if ((f.status === "model" || f.status === "instructor") && q.classification?.origin !== f.status) return false;
      if (within && !(q.classification?.nodes || []).some((/** @type {string} */ id) => within.has(id))) return false;
      if (f.difficulty && q.classification?.difficulty !== f.difficulty) return false;
      if (f.type && !(f.type === q.content.kind || (q.classification?.types || []).includes(f.type))) return false;
      if (f.source && q.origin !== f.source) return false;
      if (f.measured) {
        const m = measured(q);
        if (f.measured === "none" ? m : !m || (f.measured === "low" ? m.earned_pct >= 50 : f.measured === "middle" ? m.earned_pct < 50 || m.earned_pct >= 80 : m.earned_pct < 80)) return false;
      }
      if (query.length) { const text = words(q); if (!query.every((w) => text.includes(w))) return false; }
      return true;
    });
  }

  function tagsMarkup(/** @type {any} */ q) {
    const c = q.classification;
    if (q.tagging === "pending" || q.tagging === "running") return `<p class="bank-tags"><span class="chip" data-tone="info">Tagging…</span></p>`;
    if (q.tagging === "failed") return `<p class="bank-tags"><span class="chip" data-tone="danger" title="${e(q.tagging_error)}">Not tagged: the model's answer failed</span></p>`;
    if (!c) return `<p class="bank-tags"><span class="chip" data-tone="neutral">Not tagged yet</span></p>`;
    const chips = c.nodes.map(tagChip).join("");
    const by = c.origin === "model" ? `<span class="bank-tagged-by" title="${e(c.reason)}">Tagged by the model</span>` : `<span class="bank-tagged-by">Tagged by you</span>`;
    return `<p class="bank-tags">${chips || '<span class="bank-muted">Not placed in the outline</span>'}${by}</p>`;
  }

  function factsMarkup(/** @type {any} */ q) {
    const c = q.content;
    const facts = [state.view.vocabulary.kinds[c.kind]];
    if (c.points !== null && c.points !== undefined) facts.push(`${c.points} ${c.points === 1 ? "pt" : "pts"}`);
    const chips = facts.map((f) => `<span class="chip" data-tone="neutral">${e(f)}</span>`);
    if (q.classification?.difficulty) chips.push(`<span class="chip bank-difficulty" data-difficulty="${e(q.classification.difficulty)}" title="The model's judgment from the question's text">${e(DIFFICULTY[/** @type {"easy"} */ (q.classification.difficulty)])}</span>`);
    const m = measured(q);
    if (m) chips.push(`<span class="chip bank-measured" title="${e(`Students earned ${m.earned_pct}% of its points on average: ${plural(m.students, "student")}, ${m.terms.join(", ")}, from ${m.sources.join(" and ")}.`)}">${e(m.earned_pct)}% earned</span>`);
    for (const type of q.classification?.types || []) chips.push(`<span class="chip" data-tone="neutral">${e(typeLabel(type))}</span>`);
    if (q.edited) chips.push(`<span class="chip" data-tone="info" title="You changed this question in the bank; the original stays in its history.">Edited</span>`);
    const checks = checksOf(q);
    if (checks.length) chips.push(`<span class="chip" data-tone="warning" title="${e(checks.join(" "))}">Check</span>`);
    return chips.join("");
  }

  /** What the reading asked the instructor to check in this question, from its notes. */
  function checksOf(/** @type {any} */ q) {
    return [...String(q.content.notes || "").matchAll(CHECKS)].map((m) => m[1]).filter(Boolean);
  }

  function contentMarkup(/** @type {any} */ c) {
    const parts = c.parts.length ? `<ol class="bank-parts">${c.parts.map((/** @type {any} */ p) => `<li><span class="bank-label">(${e(p.label)})</span> ${mathText(p.text, h)}${p.points !== null && p.points !== undefined ? ` <span class="bank-muted">[${e(p.points)} pts]</span>` : ""}</li>`).join("")}</ol>` : "";
    const choices = c.choices.length ? `<ol class="bank-choices">${c.choices.map((/** @type {string} */ choice, /** @type {number} */ i) => {
      const letter = String.fromCharCode(97 + i);
      const right = c.correct_choice === letter;
      return `<li${right ? ' data-correct="true"' : ""}><span class="bank-label">(${letter})</span> ${mathText(choice, h)}${right ? ' <span class="chip" data-tone="success">Correct</span>' : ""}</li>`;
    }).join("")}</ol>` : "";
    return `<div class="bank-statement">${mathText(c.statement, h)}</div>${parts}${choices}`;
  }

  function pictureImage(/** @type {any} */ p) {
    return `<img class="bank-picture-image" src="${e(p.url)}" alt="${e(p.caption || "A picture that goes with this question")}" width="${e(p.width)}" height="${e(p.height)}" loading="lazy" decoding="async">`;
  }

  /** A question's picture with its caption, where it came from and (in the question's dialog) Remove. */
  function pictureMarkup(/** @type {any} */ p) {
    const from = p.origin === "page" ? `Cut from ${p.source_name || "a PDF"}, page ${p.page}` : `Added from ${p.source_name || "a file"}`;
    return `<figure class="bank-picture">${pictureImage(p)}<figcaption>${p.caption ? `<span>${e(p.caption)}</span>` : ""}<span class="bank-muted">${e(from)} · ${e(day(p.created_at))}</span>
      <button type="button" class="text-button" data-bank-remove-picture="${e(p.id)}">Remove</button></figcaption></figure>`;
  }

  /** The cards to show: one per question, or (grouped) one per problem with its versions, the version shown chosen by the instructor. */
  function units(/** @type {any[]} */ all) {
    if (!state.grouped) return all.map((q) => ({ shown: q, members: [q] }));
    const out = [], groups = new Map();
    for (const q of all) {
      const gid = q.group?.id;
      if (!gid) { out.push({ shown: q, members: [q] }); continue; }
      let unit = groups.get(gid);
      if (!unit) { unit = { gid, members: [] }; groups.set(gid, unit); out.push(unit); }
      unit.members.push(q);
    }
    const versions = new Map();
    for (const q of state.view.questions) {
      if (!q.group?.id) continue;
      const key = `${q.group.id}:${Boolean(q.retired)}`;
      if (!versions.has(key)) versions.set(key, []);
      versions.get(key).push(q);
    }
    for (const unit of out) {
      if (!unit.gid) continue;
      unit.members.sort((/** @type {any} */ a, /** @type {any} */ b) => (a.version || "").localeCompare(b.version || ""));
      unit.versions = (versions.get(`${unit.gid}:${Boolean(unit.members[0].retired)}`) || [])
        .sort((/** @type {any} */ a, /** @type {any} */ b) => (a.version || "").localeCompare(b.version || ""));
      unit.shown = unit.versions.find((/** @type {any} */ q) => q.id === state.pick[unit.gid]) || unit.members[0];
    }
    if (state.order) {
      const value = (/** @type {any} */ unit) => measured(unit.shown)?.earned_pct;
      out.sort((a, b) => {
        const x = value(a), y = value(b);
        if (x === undefined || y === undefined) return x === undefined ? (y === undefined ? 0 : 1) : -1;
        return state.order === "hardest" ? x - y : y - x;
      });
    }
    return out;
  }

  // COMPACT ROWS (requirement, 2026-10-05).
  // A row: where it came from, its versions, only what needs a look (to check, not tagged), then one quiet line of facts (its
  // sections by number, difficulty, % earned, format and points); under it the statement cut to two lines. The whole question,
  // its tags, types and objective open in its window.

  /** A question's sections by number (a topic or an objective counts under its section), its full tags as the hint. */
  function sectionsOf(/** @type {any} */ q) {
    const numbers = [], names = [];
    for (const id of q.classification?.nodes || []) {
      const chain = path(id);
      const section = chain.find((n) => n.level === "section"), chapter = chain.find((n) => n.level === "chapter");
      const label = section?.number || (section ? section.title : chapter ? nodeName(chapter) : "");
      if (label && !numbers.includes(label)) numbers.push(label);
      names.push(chain.map((n) => nodeName(n)).join(" › "));
    }
    numbers.sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    return { numbers, names };
  }

  function rowFactsMarkup(/** @type {any} */ q) {
    const c = q.content, facts = [];
    const { numbers, names } = sectionsOf(q);
    if (numbers.length) facts.push(`<span class="bank-tag" title="${e(names.join("\n"))}">${e(numbers.join(", "))}</span>`);
    else if (q.classification) facts.push(`<span class="bank-tag">No section</span>`);
    if (q.classification?.difficulty) facts.push(`<span title="The model's judgment from the question's text">${e(DIFFICULTY[/** @type {"easy"} */ (q.classification.difficulty)])}</span>`);
    const m = measured(q);
    if (m) facts.push(`<span class="bank-measured" title="${e(`Students earned ${m.earned_pct}% of its points on average: ${plural(m.students, "student")}, ${m.terms.join(", ")}, from ${m.sources.join(" and ")}.`)}">${e(m.earned_pct)}% earned</span>`);
    facts.push(e(state.view.vocabulary.kinds[c.kind] || ""));
    if (c.points !== null && c.points !== undefined) facts.push(`${e(c.points)} ${c.points === 1 ? "pt" : "pts"}`);
    if (q.edited) facts.push(`<span title="You changed this question in the bank; the original stays in its history.">Edited</span>`);
    return facts.filter(Boolean).join('<span class="bank-dot" aria-hidden="true"> · </span>');
  }

  /** Only what needs a look: something to check, not tagged, tagging or failed. */
  function rowFlagsMarkup(/** @type {any} */ q) {
    const flags = [];
    const checks = checksOf(q);
    if (checks.length) flags.push(`<span class="chip" data-tone="warning" title="${e(checks.join(" "))}">Check</span>`);
    if (q.tagging === "pending" || q.tagging === "running") flags.push(`<span class="chip" data-tone="info">Tagging…</span>`);
    else if (q.tagging === "failed") flags.push(`<span class="chip" data-tone="danger" title="${e(q.tagging_error)}">Not tagged: the model's answer failed</span>`);
    else if (!q.classification) flags.push(`<span class="chip" data-tone="neutral">Not tagged yet</span>`);
    return flags.join("");
  }

  function cardMarkup(/** @type {any} */ unit) {
    const q = unit.shown;
    const matching_ = new Set(unit.members.map((/** @type {any} */ m) => m.id));
    const versions = unit.versions && unit.versions.length > 1
      ? `<span class="bank-version-picks" role="group" aria-label="Versions of this problem">${unit.versions.map((/** @type {any} */ v) => `<button type="button" class="bank-version-pick" data-bank-pick="${e(unit.gid)}:${e(v.id)}" aria-pressed="${v.id === q.id}"${matching_.has(v.id) ? "" : ' data-unmatched="true" title="Does not match the filters"'}>${e(v.version || "?")}</button>`).join("")}</span>` : "";
    const c = q.content;
    // Two lines of one paragraph: line breaks become spaces and display math ($$…$$) is set inline, so the cut falls in the text.
    const text = String(c.statement || c.parts[0]?.text || "").replace(/\$\$([\s\S]+?)\$\$/g, (_, math) => `$${math}$`).replace(/\s*\n\s*/g, " ");
    return `<li class="bank-card" data-question="${e(q.id)}"${q.retired ? ' data-retired="true"' : ""}>
      <div class="bank-card-head"><button type="button" class="bank-source" data-bank-open="${e(q.id)}">${e(q.source_label)}</button>${versions}<span class="bank-tags">${rowFlagsMarkup(q)}</span><span class="bank-facts">${rowFactsMarkup(q)}</span></div>
      <div class="bank-card-body" data-bank-open="${e(q.id)}"><div class="bank-statement">${mathText(text, h)}</div>${q.pictures?.length ? `<div class="bank-card-pictures">${q.pictures.map((/** @type {any} */ p) => pictureImage(p)).join("")}</div>` : ""}</div></li>`;
  }

  function select(/** @type {string} */ name, /** @type {[string, string][]} */ options, /** @type {string} */ value, /** @type {string} */ label) {
    return `<label class="field bank-filter">${e(label)}<select data-bank-filter="${e(name)}">${options.map(([v, text]) => `<option value="${e(v)}"${v === value ? " selected" : ""}>${e(text)}</option>`).join("")}</select></label>`;
  }

  function filtersMarkup() {
    const f = state.filters, view = state.view;
    // Find, difficulty, % earned and order stay out (finding questions and how they did); the rest folds, open while one is set.
    const more = [f.type, f.source, f.status].filter(Boolean).length + (state.grouped ? 0 : 1);
    return `<div class="bank-filters" role="search">
      <label class="field bank-search">Find<input type="search" data-page-search data-bank-search placeholder="Words, numbers, an exam's name" autocomplete="off" value="${e(f.query)}"></label>
      ${select("difficulty", [["", "Any"], ...Object.entries(DIFFICULTY)], f.difficulty, "Difficulty")}
      ${select("measured", Object.entries(MEASURED), f.measured, "Earned")}
      <label class="field bank-filter">Order<select data-bank-order>${Object.entries(ORDER).map(([v, t]) => `<option value="${e(v)}"${v === state.order ? " selected" : ""}>${e(t)}</option>`).join("")}</select></label>
      <details class="bank-more-filters"${more || state.moreFilters ? " open" : ""}><summary>More filters${more ? ` <span class="bank-more-count">${more}</span>` : ""}</summary><div class="bank-filters">
        ${select("type", [["", "Any"], ...Object.entries(view.vocabulary.kinds), ...view.vocabulary.types.map((/** @type {any} */ t) => [t.key, t.label])], f.type, "Type")}
        ${select("status", Object.entries(STATUS), f.status, "Show")}
        ${select("source", [["", "Any"], ...Object.entries(SOURCES)], f.source, "Source")}
        <label class="bank-grouped"><input type="checkbox" data-bank-grouped${state.grouped ? " checked" : ""}> One row per problem, its versions together</label></div></details></div>`;
  }

  function listMarkup() {
    const all = matching();
    const cards = units(all);
    const shown = cards.slice(0, state.shown);
    const f = state.filters;
    const filtered = Boolean(f.node || f.query || f.difficulty || f.type || f.source || f.status || f.measured);
    const where = f.node ? ` under ${nodeName(nodeMap().get(f.node), { long: true })}` : "";
    const count = state.grouped && cards.length !== all.length ? `${plural(cards.length, "problem")} (${plural(all.length, "question")} with their versions)` : plural(all.length, "question");
    // What waits on the instructor: the questions a reading asked them to check, one press from the filter that lists them.
    const toCheck = f.status === "check" ? 0 : state.view.questions.filter((/** @type {any} */ q) => !q.retired && checksOf(q).length).length;
    const check = toCheck ? `<button type="button" class="chip bank-to-check" data-tone="warning" data-bank-show-check>${e(plural(toCheck, "question"))} to check</button>` : "";
    const line = `<p class="bank-result-line" role="status">${check}<span>${e(count)}${e(where)}</span>${filtered ? ` <button type="button" class="text-button" data-bank-clear>Clear filters</button>` : ""}</p>`;
    if (!state.view.questions.length) {
      return emptyMarkup({ icon: "book", title: "No questions yet", body: "Questions join by themselves once a quiz Faculty drafted is approved or an assessment's rubric is approved in Key & Rubric Finalization. You can also write one, or read them out of a PDF.",
                           action: { label: "Add a question", primary: true, data: { bankAdd: "" } } });
    }
    if (!all.length) {
      return `${line}${emptyMarkup({ kind: "filtered", title: "No question matches", body: "Choose another outline entry or clear the filters.", action: { label: "Clear filters", data: { bankClear: "" } } })}`;
    }
    const more = cards.length > shown.length ? `<button type="button" class="button bank-more" data-bank-more>Show ${Math.min(PAGE, cards.length - shown.length)} more of ${cards.length - shown.length}</button>` : "";
    return `${line}<ul class="bank-cards">${shown.map(cardMarkup).join("")}</ul>${more}`;
  }

  function uploadsMarkup() {
    const uploads = state.view.uploads;
    if (!uploads.length) return "";
    const rows = uploads.map((/** @type {any} */ u) => {
      const run = u.run;
      const status = !run ? "" : run.status === "pending" || run.status === "running" ? '<span class="chip" data-tone="info">Reading…</span>'
        : run.status === "failed" ? `<span class="chip" data-tone="danger" title="${e(run.error)}">${run.error === "Stopped by the instructor." ? "Stopped" : "Could not be read"}</span>`
        : `<span class="chip" data-tone="success">Found ${plural(run.found || 0, "question")}</span>`;
      const actions = [
        run?.status === "complete" ? `<button type="button" class="button" data-bank-review="${e(u.id)}">Review and add</button>` : "",
        run?.status === "complete" && u.waiting ? `<button type="button" class="button" data-bank-add-all="${e(u.id)}">Add all</button>` : "",
        run?.status === "failed" ? `<button type="button" class="button" data-bank-retry-run="${e(run.id)}">Read it again</button>` : "",
        run && (run.status === "pending" || run.status === "running") ? `<button type="button" class="text-button" data-bank-cancel-run="${e(run.id)}">Stop</button>` : "",
        `<a class="text-button" ${viewLink(u.url, u.name || "PDF", e)}>PDF</a>`,
      ].join("");
      // What is in the bank: added from this PDF, and whether anything it holds is still missing (problems the bank has from grading or
      // another file count as in it, so a blank exam read again says "all in the bank").
      const kept = run?.status === "complete" && run.found ? (u.waiting ? `${u.waiting} not in the bank yet` : "all in the bank") : "";
      return `<li class="bank-upload"><div class="bank-upload-text"><strong>${e(u.source_name)}</strong><small>${e([`uploaded ${day(u.created_at)}`, u.added ? `${u.added} added from it` : "", kept].filter(Boolean).join(" · "))}</small></div>${status}<div class="bank-upload-actions">${actions}</div></li>`;
    }).join("");
    return disclosureMarkup({ label: "PDFs read for questions", hint: uploads.length, key: "question-bank:uploads", open: uploads.some((/** @type {any} */ u) => u.run && u.run.status !== "complete" || u.waiting), body: `<ul class="bank-uploads">${rows}</ul>` });
  }

  function draw() {
    const node = host();
    if (!node || !state?.view) return;
    node.innerHTML = `<div class="bank-page">${notices()}<div class="bank-layout">${outlineMarkup()}<div class="bank-main">${filtersMarkup()}<div data-bank-list>${listMarkup()}</div>${uploadsMarkup()}</div></div></div>`;
    renderMath(node);
    bind(node);
    schedule();
  }

  function drawList() {
    const node = host();
    const list = node?.querySelector("[data-bank-list]");
    if (!list) return;
    list.innerHTML = listMarkup();
    renderMath(list);
  }

  function drawOutline() {
    const node = host();
    const outline = node?.querySelector(".bank-outline");
    if (!outline) return;
    const template = document.createElement("template");
    template.innerHTML = outlineMarkup();
    outline.replaceWith(/** @type {Element} */ (template.content.firstElementChild));
    bindActionMenu(/** @type {HTMLElement} */ (node));
  }

  function choose(/** @type {string} */ nodeId) {
    state.filters.node = nodeId;
    state.shown = PAGE;
    if (nodeId) for (const n of path(nodeId)) if (n.id !== nodeId) state.open.add(n.id);
    drawOutline();
    drawList();
  }

  function bind(/** @type {HTMLElement} */ node) {
    bindActionMenu(node);
    // The folded filters stay as the instructor left them when the page draws again (a poll, a write).
    node.querySelector(".bank-more-filters")?.addEventListener("toggle", (event) => { state.moreFilters = /** @type {HTMLDetailsElement} */ (event.currentTarget).open; });
    const search = /** @type {HTMLInputElement|null} */ (node.querySelector("[data-bank-search]"));
    if (search) search.oninput = () => { state.filters.query = search.value; state.shown = PAGE; drawList(); };
    node.onchange = (event) => {
      const target = /** @type {HTMLElement} */ (event.target);
      if (target.matches("[data-bank-filter]")) {
        const select_ = /** @type {HTMLSelectElement} */ (target);
        state.filters[/** @type {string} */ (select_.dataset.bankFilter)] = select_.value;
        state.shown = PAGE;
        drawList();
      } else if (target.matches("[data-bank-order]")) {
        state.order = /** @type {HTMLSelectElement} */ (target).value;
        drawList();
      } else if (target.matches("[data-bank-grouped]")) {
        state.grouped = /** @type {HTMLInputElement} */ (target).checked;
        state.shown = PAGE;
        drawList();
      } else if (target.matches("[data-bank-retired-entries]")) {
        state.retiredEntries = /** @type {HTMLInputElement} */ (target).checked;
        drawOutline();
      }
    };
    node.onclick = (event) => {
      const target = /** @type {HTMLElement} */ (event.target);
      if (target.closest("a")) return;
      const el = /** @type {HTMLElement|null} */ (target.closest("[data-bank-node],[data-bank-toggle],[data-bank-open],[data-bank-more],[data-bank-clear],[data-bank-add],[data-bank-add-node],[data-bank-rename],[data-bank-retire-node],[data-bank-review],[data-bank-add-all],[data-bank-retry-run],[data-bank-cancel-run],[data-bank-tag-failed],[data-bank-pick],[data-bank-show-check]"));
      if (!el) return;
      const d = el.dataset;
      if (el.hasAttribute("data-bank-pick")) {
        const [gid, qid] = (d.bankPick || "").split(":");
        state.pick[gid] = qid;
        drawList();
      } else if (el.hasAttribute("data-bank-toggle")) {
        const id = d.bankToggle || "";
        if (state.open.has(id)) state.open.delete(id); else state.open.add(id);
        drawOutline();
      } else if (el.hasAttribute("data-bank-node")) choose(d.bankNode || "");
      else if (el.hasAttribute("data-bank-open")) void openQuestion(d.bankOpen || "");
      else if (el.hasAttribute("data-bank-more")) { state.shown += PAGE; drawList(); }
      else if (el.hasAttribute("data-bank-show-check")) { state.filters.status = "check"; state.shown = PAGE; draw(); }
      else if (el.hasAttribute("data-bank-clear")) {
        state.filters = { node: "", query: "", difficulty: "", type: "", source: "", status: "", measured: "" };
        state.shown = PAGE;
        draw();
      } else if (el.hasAttribute("data-bank-add")) openEdit(null);
      else if (el.hasAttribute("data-bank-add-node")) openNode(null, d.bankAddNode || "chapter", d.bankParent || "");
      else if (el.hasAttribute("data-bank-rename")) openNode(nodeMap().get(d.bankRename || ""), "", "");
      else if (el.hasAttribute("data-bank-retire-node")) void retireNode(d.bankRetireNode || "", d.bankRetired === "true");
      else if (el.hasAttribute("data-bank-review")) void openReview(d.bankReview || "");
      else if (el.hasAttribute("data-bank-add-all")) void openAddAll(d.bankAddAll || "");
      else if (el.hasAttribute("data-bank-retry-run")) void post(`/runs/${encodeURIComponent(d.bankRetryRun || "")}/retry`, { request_id: newId("bank-retry") }, "Reading it again.");
      else if (el.hasAttribute("data-bank-cancel-run")) void post(`/runs/${encodeURIComponent(d.bankCancelRun || "")}/cancel`, {}, "Stopped.");
      else if (el.hasAttribute("data-bank-tag-failed")) {
        const ids = state.view.questions.filter((/** @type {any} */ q) => q.tagging === "failed" && !q.retired).map((/** @type {any} */ q) => q.id);
        void post(`/definitions/${encodeURIComponent(state.definitionId)}/tag`, { request_id: newId("bank-tag"), question_ids: ids }, `Tagging ${plural(ids.length, "question")} again.`);
      }
    };
  }

  /** A write that answers with the whole bank. */
  async function post(/** @type {string} */ path_, /** @type {any} */ body, /** @type {string} */ done) {
    const owner = state;
    try {
      const view = await api(route(path_), "POST", body);
      if (state !== owner) return null;
      accept(view);
      draw();
      refreshHeader();
      if (done) toast(done);
      return view;
    } catch (cause) {
      toast(message(cause), true);
      return null;
    }
  }

  async function reload() {
    const owner = state;
    try {
      const view = await api(route(`/definitions/${encodeURIComponent(owner.definitionId)}`));
      if (state !== owner) return;
      accept(view);
      draw();
      refreshHeader();
    } catch (cause) {
      toast(message(cause), true);
    }
  }

  /** A write that answered with the whole bank has landed: paint it if its dialog is still the open one, else read the bank again (the
   * page still shows it). */
  async function landed(/** @type {any} */ view, /** @type {() => boolean} */ current) {
    if (current()) {
      accept(view);
      dialog().close();
      draw();
    } else await reload();
  }

  function fail(/** @type {HTMLElement} */ form, /** @type {string} */ text) {
    const error = /** @type {HTMLElement|null} */ (form.querySelector(".form-error"));
    if (!error) return;
    error.textContent = text;
    error.hidden = !text;
  }

  /** Disable a form while it is sent; the callback's error is shown in the form. */
  async function sending(/** @type {HTMLFormElement} */ form, /** @type {(current: () => boolean) => Promise<void>} */ work, /** @type {string} */ busyLabel) {
    const context = forms.get(form);
    const current = () => context?.owner === state && context.active();
    if (!current()) return;
    if (form.dataset.saving === "true") return;
    form.dataset.saving = "true";
    const buttons = [...form.querySelectorAll("button")].filter((button) => !button.disabled);
    const submit = /** @type {HTMLButtonElement|null} */ (form.querySelector('button[type="submit"]'));
    const label = submit?.textContent || "";
    buttons.forEach((b) => { b.disabled = true; });
    if (submit && busyLabel) submit.textContent = busyLabel;
    fail(form, "");
    try {
      await work(current);
    } catch (cause) {
      // A write that failed after its dialog closed still says so (the dialog's error line is gone).
      if (current()) fail(form, message(cause)); else toast(message(cause), true);
    } finally {
      form.dataset.saving = "false";
      buttons.forEach((b) => { b.disabled = false; });
      if (submit) submit.textContent = label;
    }
  }

  // -- One question -------------------------------------------------------------------------------------------------------------
  async function openQuestion(/** @type {string} */ id) {
    const current = captureDialogIntent(dialog());
    let q;
    try {
      q = await api(route(`/questions/${encodeURIComponent(id)}`));
      if (!current()) return;
    } catch (cause) {
      toast(message(cause), true);
      return;
    }
    const c = q.content;
    const answers = c.final_answers.length ? `<div class="bank-detail-block"><h4>Final answers</h4><ul class="bank-answers">${c.final_answers.map((/** @type {any} */ a) => `<li>${a.part ? `<span class="bank-label">${e(answerLabel(a.part))}</span> ` : ""}${answerMarkup(a.answer)}</li>`).join("")}</ul></div>` : "";
    const solution = c.solution ? disclosureMarkup({ label: "Worked solution", key: "question-bank:solution", body: `<div class="bank-solution">${mathText(c.solution, h)}</div>` }) : "";
    const figure = c.figure ? disclosureMarkup({ label: "Figure (TikZ source, as printed)", body: `<pre class="bank-code">${e(c.figure)}</pre>` }) : "";
    const shown = q.pictures.filter((/** @type {any} */ p) => !p.removed);
    const removed = q.pictures.filter((/** @type {any} */ p) => p.removed);
    const mentioned = /^Figure:/.test(c.notes || "");
    const pictures = `<div class="bank-detail-block"><h4>Pictures</h4>${shown.length ? `<div class="bank-pictures">${shown.map(pictureMarkup).join("")}</div>`
      : `<p class="bank-muted">${mentioned ? "The PDF's reading says this question relies on a figure. Cut it from the page so it stays with the question." : "No pictures. Add a graph, diagram or picture the question relies on."}</p>`}
      ${removed.length ? `<ul class="bank-removed-pictures">${removed.map((/** @type {any} */ p) => `<li><span class="bank-muted">Removed: ${e(p.caption || (p.origin === "page" ? `cut from page ${p.page}` : p.source_name || "a picture"))}</span> <button type="button" class="text-button" data-bank-restore-picture="${e(p.id)}">Restore</button></li>`).join("")}</ul>` : ""}
      <div class="bank-detail-actions">${state.view.uploads.length ? `<button type="button" class="button" data-bank-cut-picture>Cut from a PDF page…</button>` : ""}<button type="button" class="button" data-bank-add-picture>Add a picture…</button></div></div>`;
    const notes = c.notes ? `<p class="bank-notes">${mathText(c.notes, h)}</p>` : "";
    const linkedBy = !q.group ? "" : q.group.origin === "model" ? `Linked by the model${q.group.reason ? `: ${q.group.reason}` : "."}`
      : q.group.origin === "source" ? "Linked from the drafted quiz, which prints this problem on each version." : "Linked by you.";
    const versions = `<div class="bank-detail-block"><h4>Versions of this problem</h4>${q.other_versions.length
      ? `<ul class="bank-version-list">${q.other_versions.map((/** @type {any} */ v) => `<li><button type="button" class="text-button" data-bank-open-version="${e(v.id)}">${e(v.version ? `Version ${v.version}` : "Version ?")}</button> <span class="bank-muted">${e(v.source_label)}${v.retired ? " · retired" : ""}</span></li>`).join("")}</ul><p class="bank-muted">${e(linkedBy)}</p>`
      : `<p class="bank-muted">${q.group ? "No other version is in the bank yet." : "Not linked to other versions."}</p>`}
      <div class="bank-detail-actions"><button type="button" class="button" data-bank-link-versions>Link versions…</button>${q.group ? `<button type="button" class="text-button" data-bank-unlink>Unlink this version</button>` : ""}</div></div>`;
    const where = q.printed?.title ? `Printed as ${[q.printed.title, q.printed.version ? `Version ${q.printed.version}` : "", q.printed.number ? `Problem ${q.printed.number}` : ""].filter(Boolean).join(" · ")}` : "Not linked to an assessment in Faculty";
    const printed = q.printed?.from === "grading" ? "" : `<p class="bank-printed"><span class="bank-muted">${e(where)}${q.source_file ? ` · read from ${e(q.source_file)}` : ""}</span> <button type="button" class="text-button" data-bank-printed>${q.printed?.title ? "Change…" : "Say where it was printed…"}</button></p>`;
    const m = measured(q);
    const stat = m ? `<p class="bank-muted">Measured: students earned ${e(m.earned_pct)}% of its points (${e(plural(m.students, "student"))}, ${e(m.terms.join(", "))}, from ${e(m.sources.join(" and "))}).${q.classification?.difficulty ? ` The model judged it ${e(q.classification.difficulty)} from its text.` : ""}</p>` : "";
    const used = q.terms?.length ? `Used in ${q.terms.join(", ")}` : "";
    const history = disclosureMarkup({ label: "History", hint: q.revisions.length + q.classifications.length + q.events.length + q.picture_events.length, body: `<ul class="bank-history">${[
      ...q.revisions.map((/** @type {any} */ r) => ({ at: r.created_at, text: `Revision ${r.revision}: ${r.origin === "instructor" ? "edited by you" : String(r.source_ref || "").startsWith("answer-key:") ? "its correct choice is the answer key's" : r.revision === 1 ? "kept from its source" : "its source was revised"}` })),
      ...q.classifications.map((/** @type {any} */ c_) => ({ at: c_.created_at, text: `Tags ${c_.revision}: ${c_.origin === "model" ? "by the model" : "by you"}${c_.nodes.length ? ` · ${c_.nodes.map((/** @type {string} */ n) => nodeName(nodeMap().get(n))).filter(Boolean).join(", ")}` : ""}${c_.difficulty ? ` · ${c_.difficulty}` : ""}` })),
      ...q.events.map((/** @type {any} */ ev) => ({ at: ev.created_at, text: ev.action === "retired" ? "Retired" : "Restored" })),
      ...q.picture_events.map((/** @type {any} */ ev) => ({ at: ev.created_at, text: `${ev.action === "added" ? "Picture added" : ev.action === "removed" ? "Picture removed" : "Picture restored"}${ev.caption ? `: ${ev.caption}` : ""}` })),
    ].sort((a, b) => b.at.localeCompare(a.at)).map((item) => `<li><span class="bank-muted">${e(day(item.at))}</span> ${e(item.text)}</li>`).join("")}</ul>` });
    const reason = q.classification?.origin === "model" && q.classification.reason ? `<p class="bank-muted">The model: ${e(q.classification.reason)}</p>` : "";
    showDialog(`<form id="question-bank-question" class="bank-dialog" data-question-id="${e(q.id)}" novalidate>${dialogHeader(q.source_label, [used, q.retired ? "Retired" : ""].filter(Boolean).join(" · "))}
      <div class="dialog-body">
        <p class="bank-facts">${factsMarkup(q)}</p>${stat}${printed}
        <div class="bank-detail-content">${contentMarkup(c)}</div>${pictures}${figure}${notes}${answers}${solution}${versions}
        <div class="bank-detail-block"><h4>Where it belongs</h4>${tagsMarkup(q)}${reason}
          <div class="bank-detail-actions"><button type="button" class="button" data-bank-classify>Change tags…</button><button type="button" class="text-button" data-bank-retag${state.view.model.ready ? "" : " disabled"}>Ask the model to tag it again</button></div></div>
        ${history}<p class="form-error" role="alert" hidden></p>
      </div>
      <footer class="dialog-footer">${q.retired ? `<button type="button" class="button" data-bank-restore>Restore</button>` : `<button type="button" class="button danger" data-bank-retire>Retire</button>`}<button type="button" class="button" data-bank-edit>Edit…</button><button type="button" class="button primary" data-action="close">Close</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (dialog().querySelector("#question-bank-question"));
    renderMath(form);
    form.onsubmit = (event) => event.preventDefault();
    form.onclick = async (event) => {
      const target = /** @type {HTMLElement} */ ((/** @type {HTMLElement} */ (event.target)).closest("button"));
      if (!target) return;
      if (target.dataset.bankOpenVersion) void openQuestion(target.dataset.bankOpenVersion);
      else if (target.hasAttribute("data-bank-classify")) openClassify(q);
      else if (target.hasAttribute("data-bank-link-versions")) openLinkVersions(q);
      else if (target.hasAttribute("data-bank-printed")) openPrinted(q);
      else if (target.hasAttribute("data-bank-unlink")) {
        await sending(form, async (current) => {
          await api(route(`/questions/${encodeURIComponent(q.id)}/versions`), "POST", { request_id: newId("bank-unlink"), based_on: q.group_revision, unlink: true });
          await reload();
          toast("Unlinked. The model never links it again; link it yourself any time.");
          if (current()) void openQuestion(q.id);
        }, "");
      }
      else if (target.hasAttribute("data-bank-edit")) openEdit(q);
      else if (target.hasAttribute("data-bank-add-picture")) openAddPicture(q);
      else if (target.hasAttribute("data-bank-cut-picture")) openCutPicture(q);
      else if (target.dataset.bankRemovePicture) {
        const pictureId = target.dataset.bankRemovePicture;
        await sending(form, async (current) => {
          await api(route(`/pictures/${encodeURIComponent(pictureId)}/remove`), "POST", { request_id: newId("bank-picture-remove") });
          await reload();
          if (current()) void openQuestion(q.id);
          // Reversible, so no confirmation first: Restore stays under Pictures (the open dialog keeps a toast's button out of reach).
          toast("The picture was removed. Restore it under Pictures.");
        }, "");
      } else if (target.dataset.bankRestorePicture) {
        const pictureId = target.dataset.bankRestorePicture;
        await sending(form, async (current) => {
          await api(route(`/pictures/${encodeURIComponent(pictureId)}/restore`), "POST", { request_id: newId("bank-picture-restore") });
          await reload();
          if (current()) void openQuestion(q.id);
          toast("The picture is back with its question.");
        }, "");
      }
      else if (target.hasAttribute("data-bank-retag")) {
        await sending(form, async (current) => {
          const view = await api(route(`/definitions/${encodeURIComponent(state.definitionId)}/tag`), "POST", { request_id: newId("bank-tag"), question_ids: [q.id] });
          await landed(view, current);
          toast("The model is tagging the question.");
        }, "");
      } else if (target.hasAttribute("data-bank-retire") || target.hasAttribute("data-bank-restore")) {
        const retire = target.hasAttribute("data-bank-retire");
        await sending(form, async (current) => {
          await api(route(`/questions/${encodeURIComponent(q.id)}/${retire ? "retire" : "restore"}`), "POST", { request_id: newId("bank-retire") });
          if (current()) dialog().close();
          await reload();
          toast(retire ? "The question was retired." : "The question is back in the bank.", retire ? { action: { label: "Restore", run: () => { void restoreQuestion(q.id); } } } : undefined);
        }, "");
      }
    };
  }

  /** Choose the questions that are versions of the same problem as `q`: those printed on the same assessment first. */
  function openLinkVersions(/** @type {any} */ q) {
    const current = new Set([q.id, ...q.other_versions.map((/** @type {any} */ v) => v.id)]);
    const candidates = state.view.questions.filter((/** @type {any} */ o) => !current.has(o.id) && !o.retired)
      .sort((/** @type {any} */ a, /** @type {any} */ b) => Number(b.assignment_id === q.assignment_id) - Number(a.assignment_id === q.assignment_id)
        || a.source_label.localeCompare(b.source_label, undefined, { numeric: true }));
    showDialog(`<form id="question-bank-link" class="bank-dialog" novalidate>${dialogHeader("Link versions", q.source_label)}
      <div class="dialog-body"><p class="field-hint">Tick the questions that ask this same problem on other versions. Each joins this question's versions, leaving any other link; the model never changes your links.</p>
        <label class="field">Find<input type="search" data-bank-link-search placeholder="An exam's name, a problem number, words" autocomplete="off"></label>
        <ul class="bank-picker bank-link-picker">${candidates.map((/** @type {any} */ o) => `<li data-search="${e(`${o.source_label} ${o.content.statement}`.toLocaleLowerCase())}"><label><input type="checkbox" name="with" value="${e(o.id)}"> <span><strong>${e(o.source_label)}</strong> <span class="bank-muted">${e(o.content.statement.slice(0, 90))}</span></span></label></li>`).join("")}</ul>
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Link</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (dialog().querySelector("#question-bank-link"));
    const search = /** @type {HTMLInputElement} */ (form.querySelector("[data-bank-link-search]"));
    search.oninput = () => {
      const terms = search.value.toLocaleLowerCase().split(/\s+/).filter(Boolean);
      form.querySelectorAll(".bank-link-picker li").forEach((li) => {
        const item = /** @type {HTMLElement} */ (li);
        item.hidden = !terms.every((t) => (item.dataset.search || "").includes(t)) && !(/** @type {HTMLInputElement} */ (item.querySelector("input"))).checked;
      });
    };
    const requestId = newId("bank-link");
    form.onsubmit = (event) => {
      event.preventDefault();
      const chosen = new FormData(form).getAll("with").map(String);
      if (!chosen.length) { fail(form, "Tick at least one question."); return; }
      void sending(form, async (current) => {
        await api(route(`/questions/${encodeURIComponent(q.id)}/versions`), "POST", { request_id: requestId, based_on: q.group_revision, with: chosen });
        await reload();
        toast(`Linked ${plural(chosen.length, "version")}.`);
        if (current()) void openQuestion(q.id);
      }, "Linking…");
    };
  }

  /** Where a question read from a PDF (or written here) was printed: an assignment of the course, its version and number. */
  function openPrinted(/** @type {any} */ q) {
    const p = q.printed || {};
    showDialog(`<form id="question-bank-printed" novalidate>${dialogHeader("Where it was printed", q.source_label)}
      <div class="dialog-body"><p class="field-hint">Its grades are then found and its versions linked with that assessment's other questions.</p>
        <label class="field">Assignment<select name="assignment_id"><option value="">Not printed on an assessment in Faculty</option>${state.view.assignments.map((/** @type {any} */ a) => `<option value="${e(a.id)}"${a.id === p.assignment_id ? " selected" : ""}>${e(a.title)}</option>`).join("")}</select></label>
        <div class="bank-row"><label class="field">Version<select name="exam_version"><option value="">None</option>${LETTERS.map((l) => `<option value="${l}"${l === p.version ? " selected" : ""}>${l}</option>`).join("")}</select></label>
          <label class="field">Problem number<input name="number" maxlength="20" value="${e(p.number || q.content.number || "")}" autocomplete="off"></label></div>
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Save</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (dialog().querySelector("#question-bank-printed"));
    const requestId = newId("bank-printed");
    form.onsubmit = (event) => {
      event.preventDefault();
      const data = new FormData(form);
      void sending(form, async (current) => {
        await api(route(`/questions/${encodeURIComponent(q.id)}/printed`), "POST", { request_id: requestId, based_on: p.revision || 0,
          assignment_id: String(data.get("assignment_id") || ""), exam_version: String(data.get("exam_version") || ""), number: String(data.get("number") || "") });
        await reload();
        void loadStats();
        toast("Saved where it was printed.");
        if (current()) void openQuestion(q.id);
      }, "Saving…");
    };
  }

  /** The figure the PDF's reading described ("Figure: …" in the notes), as a caption to start from. */
  function figureCaption(/** @type {any} */ q) {
    const match = /^Figure:[ \t]*([^\n]*)/.exec(q.content.notes || "");
    return match ? match[1].trim().slice(0, 1000) : "";
  }

  /** Add an image file (a screenshot, an exported graph) to a question. */
  function openAddPicture(/** @type {any} */ q) {
    const requestId = newId("bank-picture");
    showDialog(`<form id="question-bank-picture" novalidate>${dialogHeader("Add a picture", q.source_label)}
      <div class="dialog-body">
        <label class="field">Picture<input type="file" name="file" accept="image/png,image/jpeg,image/gif,image/webp" required><span class="field-hint">A PNG, JPEG, GIF or WebP image up to ${PICTURE_MB} MB; a screenshot works.</span></label>
        <label class="field">Caption<input name="caption" maxlength="1000" value="${e(figureCaption(q))}" autocomplete="off"><span class="field-hint">What it shows, for anyone who cannot see it.</span></label>
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Add</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (dialog().querySelector("#question-bank-picture"));
    form.onsubmit = (event) => {
      event.preventDefault();
      const file = (/** @type {HTMLInputElement} */ (form.elements.namedItem("file"))).files?.[0];
      if (!file) { fail(form, "Choose the picture."); return; }
      if (file.size > PICTURE_MB * 1048576) { fail(form, `That picture is larger than ${PICTURE_MB} MB. Save a smaller copy, then add that.`); return; }
      const data = new FormData();
      data.append("request_id", requestId);
      data.append("caption", String(new FormData(form).get("caption") || ""));
      data.append("file", file, file.name);
      void sending(form, async (current) => {
        await api(route(`/questions/${encodeURIComponent(q.id)}/pictures`), "POST", data, {}, LONG);
        await reload();
        toast("The picture was added.");
        if (current()) void openQuestion(q.id);
      }, "Adding…");
    };
  }

  /** Cut a picture from a page of a PDF read for the bank: drag a box over the page. */
  function openCutPicture(/** @type {any} */ q) {
    const uploads = state.view.uploads.filter((/** @type {any} */ u) => u.pages);
    if (!uploads.length) { toast("No PDF read for the bank has its pages ready yet.", true); return; }
    let upload = uploads.find((/** @type {any} */ u) => u.id === q.upload_id) || uploads[0];
    let page = upload.id === q.upload_id && q.source_page ? q.source_page : 1;
    /** @type {{left: number, top: number, right: number, bottom: number}|null} */ let box = null;
    const requestId = newId("bank-picture-cut");
    showDialog(`<form id="question-bank-cut" class="bank-dialog" novalidate>${dialogHeader("Cut a picture from a PDF page", q.source_label)}
      <div class="dialog-body">
        <div class="bank-row"><label class="field">PDF<select name="upload">${uploads.map((/** @type {any} */ u) => `<option value="${e(u.id)}"${u.id === upload.id ? " selected" : ""}>${e(u.source_name)}</option>`).join("")}</select></label>
          <div class="field bank-cut-page"><label for="bank-cut-page-number">Page</label><span class="bank-cut-pager"><button type="button" class="button" data-bank-page="-1" aria-label="Previous page">‹</button><input id="bank-cut-page-number" name="page" type="number" min="1" value="${page}" inputmode="numeric"><button type="button" class="button" data-bank-page="1" aria-label="Next page">›</button></span><span class="field-hint" data-bank-page-count></span></div></div>
        <p class="field-hint">Drag a box around the graph or picture, with its axes and labels. Leave answers out.</p>
        <div class="bank-cut-stage" data-bank-cut-stage><img src="${route(`/uploads/${encodeURIComponent(upload.id)}/pages/${page}.jpg`)}" data-bank-cut-page alt="The PDF page to cut the picture from" draggable="false"><div class="bank-cut-box" data-bank-cut-box hidden></div></div>
        <label class="field">Caption<input name="caption" maxlength="1000" value="${e(figureCaption(q))}" autocomplete="off"><span class="field-hint">What it shows, for anyone who cannot see it.</span></label>
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary" disabled>Cut and add</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (dialog().querySelector("#question-bank-cut"));
    const image = /** @type {HTMLImageElement} */ (form.querySelector("[data-bank-cut-page]"));
    const stage = /** @type {HTMLElement} */ (form.querySelector("[data-bank-cut-stage]"));
    const frame = /** @type {HTMLElement} */ (form.querySelector("[data-bank-cut-box]"));
    const pageInput = /** @type {HTMLInputElement} */ (form.elements.namedItem("page"));
    const submit = /** @type {HTMLButtonElement} */ (form.querySelector('button[type="submit"]'));
    const showBox = () => {
      frame.hidden = !box;
      submit.disabled = !box || (box.right - box.left) < 0.02 || (box.bottom - box.top) < 0.02;
      if (!box) return;
      Object.assign(frame.style, { left: `${box.left * 100}%`, top: `${box.top * 100}%`, width: `${(box.right - box.left) * 100}%`, height: `${(box.bottom - box.top) * 100}%` });
    };
    const showPage = () => {
      page = Math.min(Math.max(1, Math.round(Number(pageInput.value) || 1)), upload.pages);
      pageInput.value = String(page);
      pageInput.max = String(upload.pages);
      (/** @type {HTMLElement} */ (form.querySelector("[data-bank-page-count]"))).textContent = `of ${upload.pages}`;
      image.src = route(`/uploads/${encodeURIComponent(upload.id)}/pages/${page}.jpg`);
      box = null;
      showBox();
    };
    showPage();
    form.onchange = (event) => {
      const target = /** @type {HTMLElement} */ (event.target);
      if (target.getAttribute("name") === "upload") {
        upload = uploads.find((/** @type {any} */ u) => u.id === (/** @type {HTMLSelectElement} */ (target)).value) || upload;
        pageInput.value = "1";
        showPage();
      } else if (target === pageInput) showPage();
    };
    form.onclick = (event) => {
      const step = /** @type {HTMLElement|null} */ ((/** @type {HTMLElement} */ (event.target)).closest("[data-bank-page]"));
      if (!step) return;
      pageInput.value = String(page + Number(step.dataset.bankPage));
      showPage();
    };
    /** @type {{x: number, y: number}|null} */ let start = null;
    const at = (/** @type {PointerEvent} */ event) => {
      const rect = image.getBoundingClientRect();
      return { x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)), y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)) };
    };
    stage.onpointerdown = (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      stage.setPointerCapture(event.pointerId);
      start = at(event);
      box = { left: start.x, top: start.y, right: start.x, bottom: start.y };
      showBox();
    };
    stage.onpointermove = (event) => {
      if (!start) return;
      const point = at(event);
      box = { left: Math.min(start.x, point.x), top: Math.min(start.y, point.y), right: Math.max(start.x, point.x), bottom: Math.max(start.y, point.y) };
      showBox();
    };
    stage.onpointerup = stage.onpointercancel = () => { start = null; };
    form.onsubmit = (event) => {
      event.preventDefault();
      if (!box) { fail(form, "Drag a box around the picture first."); return; }
      const region = box;
      const caption = String(new FormData(form).get("caption") || "");
      void sending(form, async (current) => {
        await api(route(`/questions/${encodeURIComponent(q.id)}/pictures/cut`), "POST", { request_id: requestId, upload_id: upload.id, page, region, caption });
        await reload();
        toast("The picture was cut from the page and added.");
        if (current()) void openQuestion(q.id);
      }, "Cutting…");
    };
  }

  async function restoreQuestion(/** @type {string} */ id) {
    try {
      await api(route(`/questions/${encodeURIComponent(id)}/restore`), "POST", { request_id: newId("bank-restore") });
      await reload();
      toast("The question is back in the bank.");
    } catch (cause) {
      toast(message(cause), true);
    }
  }

  /** The outline as checkboxes, for choosing where a question belongs. */
  function pickerMarkup(/** @type {string[]} */ chosen) {
    const standing = nodes().filter((/** @type {any} */ n) => !path(n.id).some((p) => p.retired));
    if (!standing.length) return `<p class="bank-muted">The outline is empty. Upload the lecture notes or add chapters first.</p>`;
    return `<label class="field">Find an entry<input type="search" data-bank-picker-search placeholder="Number or title" autocomplete="off"></label>
      <ul class="bank-picker">${standing.map((/** @type {any} */ n) => `<li data-depth="${Math.min(n.depth, 3)}" data-search="${e(path(n.id).map((p) => nodeName(p, { long: true })).join(" ").toLocaleLowerCase())}"><label><input type="checkbox" name="nodes" value="${e(n.id)}"${chosen.includes(n.id) ? " checked" : ""}> ${n.level === "objective" ? '<span class="bank-node-kind">Objective</span> ' : ""}${e(nodeName(n, { long: true }))}</label></li>`).join("")}</ul>`;
  }

  function tagFieldsMarkup(/** @type {any} */ c) {
    const types = state.view.vocabulary.types;
    return `<fieldset class="bank-fieldset"><legend>Where it belongs</legend>${pickerMarkup(c?.nodes || [])}</fieldset>
      <fieldset class="bank-fieldset bank-inline"><legend>Difficulty</legend>${[["", "Not set"], ...Object.entries(DIFFICULTY)].map(([v, t]) => `<label><input type="radio" name="difficulty" value="${e(v)}"${(c?.difficulty || "") === v ? " checked" : ""}> ${e(t)}</label>`).join("")}</fieldset>
      <fieldset class="bank-fieldset bank-inline"><legend>Question type</legend>${types.map((/** @type {any} */ t) => `<label title="${e(t.description)}"><input type="checkbox" name="types" value="${e(t.key)}"${(c?.types || []).includes(t.key) ? " checked" : ""}> ${e(t.label)}</label>`).join("")}</fieldset>`;
  }

  function bindPicker(/** @type {HTMLElement} */ form) {
    const search = /** @type {HTMLInputElement|null} */ (form.querySelector("[data-bank-picker-search]"));
    if (!search) return;
    search.oninput = () => {
      const terms = search.value.toLocaleLowerCase().split(/\s+/).filter(Boolean);
      form.querySelectorAll(".bank-picker li").forEach((li) => {
        const item = /** @type {HTMLElement} */ (li);
        item.hidden = !terms.every((t) => (item.dataset.search || "").includes(t)) && !(/** @type {HTMLInputElement} */ (item.querySelector("input"))).checked;
      });
    };
  }

  function readTags(/** @type {HTMLFormElement} */ form) {
    const data = new FormData(form);
    return { nodes: data.getAll("nodes").map(String), difficulty: String(data.get("difficulty") || ""), types: data.getAll("types").map(String) };
  }

  function openClassify(/** @type {any} */ q) {
    showDialog(`<form id="question-bank-tags" class="bank-dialog" novalidate>${dialogHeader("Change tags", q.source_label)}
      <div class="dialog-body"><p class="field-hint">Your tags stand until you change them or ask the model to tag the question again.</p>${tagFieldsMarkup(q.classification)}<p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Save tags</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (dialog().querySelector("#question-bank-tags"));
    bindPicker(form);
    const requestId = newId("bank-classify");
    form.onsubmit = (event) => {
      event.preventDefault();
      void sending(form, async (current) => {
        await api(route(`/questions/${encodeURIComponent(q.id)}/classify`), "POST", { request_id: requestId, based_on: q.classification?.revision || 0, ...readTags(form) });
        if (current()) dialog().close();
        await reload();
        toast("Tags saved.");
      }, "Saving…");
    };
  }

  // -- Writing and editing a question ---------------------------------------------------------------------------------------------
  const lines = (/** @type {string} */ text) => text.split("\n").map((l) => l.trim()).filter(Boolean);
  const LABELLED = /^\(?([a-z])[).:]\s*(.*)$/i;
  /** A final answer's label: a part's letter as (a), an answer key's own label ("corner points", "C(x)") as label: */
  const answerLabel = (/** @type {string} */ part) => (/^[a-z]$/i.test(part) ? `(${part})` : `${part}:`);
  /** An answer key's value is often bare LaTeX ("3 \\times 2", "\\dfrac{168}{11}"): set as mathematics unless it is mostly words
   *  ("Not possible", "(c) or Minimum value C = 68 at (12,4)"), which stay text. Delimited mathematics is set either way. */
  const formula = (/** @type {string} */ answer) =>
    !/[A-Za-z]{3,}(\s|$)/.test(answer.replace(/\\(?:text|mathrm|textbf|mbox)\s*\{[^{}]*\}/g, " ").replace(/\\[A-Za-z]+/g, " "));
  /** An answer key joins its accepted forms with " or ": each is set by itself. */
  const answerMarkup = (/** @type {string} */ answer) => answer.split(" or ").map((form) => mathText(form, h, formula(form))).join(' <span class="bank-muted">or</span> ');
  /** "(a) answer" or "label: answer" (the label without $ and at most 60 characters), else an answer to the whole question. */
  function readAnswer(/** @type {string} */ line) {
    const letter = line.match(/^\(([a-z])\)\s+(.*)$/i);
    if (letter) return { part: letter[1], answer: letter[2] };
    const named = line.match(/^([^$:]{1,60}):\s+(.+)$/);
    return named ? { part: named[1].trim(), answer: named[2] } : { part: "", answer: line };
  }

  function openEdit(/** @type {any} */ q) {
    const c = q?.content || { kind: "free_response", number: "", statement: "", parts: [], choices: [], correct_choice: "", figure: "", points: null, final_answers: [], solution: "", notes: "" };
    const partsText = c.parts.map((/** @type {any} */ p) => `(${p.label}) ${p.text}`).join("\n");
    const answersText = c.final_answers.map((/** @type {any} */ a) => `${a.part ? `${answerLabel(a.part)} ` : ""}${a.answer}`).join("\n");
    const letters = (/** @type {number} */ n) => Array.from({ length: n }, (_, i) => String.fromCharCode(97 + i));
    showDialog(`<form id="question-bank-edit" class="bank-dialog bank-edit" novalidate>${dialogHeader(q ? "Edit the question" : "Add a question", q ? q.source_label : [state.code, "every term"].join(" · "))}
      <div class="dialog-body">
        ${q && q.origin !== "instructor" ? `<p class="field-hint">Your edit is kept in the bank only; ${q.origin === "pdf" ? "the PDF" : "the graded question"} is not changed. The earlier wording stays in its history.</p>` : ""}
        <fieldset class="bank-fieldset bank-inline"><legend>Kind</legend>${Object.entries(state.view.vocabulary.kinds).map(([v, t]) => `<label><input type="radio" name="kind" value="${e(v)}"${c.kind === v ? " checked" : ""}> ${e(t)}</label>`).join("")}</fieldset>
        <div class="bank-row"><label class="field">Number<input name="number" maxlength="20" value="${e(c.number)}" autocomplete="off"><span class="field-hint">As printed, optional.</span></label>
          <label class="field">Points<input name="points" inputmode="decimal" value="${c.points ?? ""}" autocomplete="off"></label></div>
        <label class="field">Statement<textarea name="statement" rows="5" required>${e(c.statement)}</textarea><span class="field-hint">Mathematics in LaTeX between $ and $, as in $\\frac{1}{2}x + 3$.</span></label>
        <div class="bank-preview" aria-live="polite" data-bank-preview></div>
        <label class="field">Parts<textarea name="parts" rows="3" placeholder="(a) Find the slope.&#10;(b) Interpret it.">${e(partsText)}</textarea><span class="field-hint">One part per line, starting with its letter.</span></label>
        <div data-bank-choices${c.kind === "multiple_choice" ? "" : " hidden"}>
          <label class="field">Choices<textarea name="choices" rows="4" placeholder="$x = 1$&#10;$x = 2$">${e(c.choices.join("\n"))}</textarea><span class="field-hint">One choice per line, in order; they are lettered (a), (b) …</span></label>
          <label class="field">Correct choice<select name="correct_choice"><option value="">Not given</option>${letters(Math.max(c.choices.length, 5)).map((l) => `<option value="${l}"${c.correct_choice === l ? " selected" : ""}>(${l})</option>`).join("")}</select></label>
        </div>
        <label class="field">Final answers<textarea name="final_answers" rows="2" placeholder="(a) $m = 2$">${e(answersText)}</textarea><span class="field-hint">One per line. Start with a part's letter, as (a), or a label and a colon, as corner points:</span></label>
        <label class="field">Worked solution<textarea name="solution" rows="4">${e(c.solution)}</textarea></label>
        <label class="field">Notes<textarea name="notes" rows="2">${e(c.notes)}</textarea></label>
        ${c.figure ? `<label class="field">Figure (TikZ source)<textarea name="figure" rows="4" class="bank-code">${e(c.figure)}</textarea></label>` : ""}
        ${q ? "" : `${disclosureMarkup({ label: "Tags (optional)", body: `<p class="field-hint">Leave them empty and the model tags the question.</p>${tagFieldsMarkup(null)}` })}`}
        <p class="form-error" role="alert" hidden></p>
      </div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${q ? "Save" : "Add to the bank"}</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (dialog().querySelector("#question-bank-edit"));
    bindPicker(form);
    const preview = /** @type {HTMLElement} */ (form.querySelector("[data-bank-preview]"));
    const statement = /** @type {HTMLTextAreaElement} */ (form.elements.namedItem("statement"));
    let pending = 0;
    const show = () => {
      preview.innerHTML = statement.value.trim() ? `<span class="bank-muted">Preview</span> ${mathText(statement.value, h)}` : "";
      renderMath(preview);
    };
    statement.oninput = () => { window.clearTimeout(pending); pending = window.setTimeout(show, 250); };
    show();
    form.onchange = (event) => {
      const target = /** @type {HTMLInputElement} */ (event.target);
      if (target.name === "kind") /** @type {HTMLElement} */ (form.querySelector("[data-bank-choices]")).hidden = target.value !== "multiple_choice";
    };
    const requestId = newId(q ? "bank-edit" : "bank-add");
    form.onsubmit = (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const worth = new Map(c.parts.map((/** @type {any} */ p) => [p.label, p.points]));
      const parts = lines(String(data.get("parts") || "")).map((line, index) => {
        const match = line.match(LABELLED);
        const label = match ? match[1].toLowerCase() : String.fromCharCode(97 + index);
        return { label, text: match ? match[2] : line, points: worth.get(label) ?? null };
      });
      const answers = lines(String(data.get("final_answers") || "")).map(readAnswer);
      const kind = String(data.get("kind") || "free_response");
      const content = {
        kind, number: String(data.get("number") || "").trim(), statement: String(data.get("statement") || ""), parts,
        choices: kind === "multiple_choice" ? lines(String(data.get("choices") || "")) : [], correct_choice: kind === "multiple_choice" ? String(data.get("correct_choice") || "") : "",
        points: String(data.get("points") || "").trim() === "" ? null : String(data.get("points")).trim(),
        final_answers: answers, solution: String(data.get("solution") || ""), notes: String(data.get("notes") || ""),
        figure: data.has("figure") ? String(data.get("figure") || "") : c.figure,
      };
      if (!content.statement.trim()) { fail(form, "Write the statement."); return; }
      void sending(form, async (current) => {
        const saved = q
          ? await api(route(`/questions/${encodeURIComponent(q.id)}/revise`), "POST", { request_id: requestId, based_on: q.revision, content })
          : await api(route(`/definitions/${encodeURIComponent(state.definitionId)}/questions`), "POST", { request_id: requestId, content, classification: readTags(form) });
        if (current()) dialog().close();
        await reload();
        toast(q ? "Saved. The earlier wording stays in the question's history." : saved.classification ? "Added to the bank with your tags." : "Added to the bank. The model tags it within a minute.");
      }, "Saving…");
    };
  }

  // -- The outline's entries ------------------------------------------------------------------------------------------------------
  function openNode(/** @type {any} */ node, /** @type {string} */ level, /** @type {string} */ parentId) {
    const parent = parentId ? nodeMap().get(parentId) : null;
    const kind = node ? node.level : level;
    const title = node ? `Rename ${LEVELS[/** @type {"chapter"} */ (kind)].toLowerCase()}` : `Add a ${LEVELS[/** @type {"chapter"} */ (kind)].toLowerCase()}`;
    const numbered = kind === "chapter" || kind === "section";
    showDialog(`<form id="question-bank-node" novalidate>${dialogHeader(title, parent ? `Under ${nodeName(parent, { long: true })}` : node ? nodeName(node, { long: true }) : [state.code, "every term"].join(" · "))}
      <div class="dialog-body">
        ${numbered ? `<label class="field">Number<input name="number" maxlength="20" value="${e(node?.number || "")}" autocomplete="off" placeholder="${kind === "chapter" ? "3" : "3.2"}"></label>` : ""}
        <label class="field">${kind === "objective" ? "Learning objective" : "Title"}<input name="title" maxlength="300" required value="${e(node?.title || "")}" autocomplete="off"${kind === "objective" ? ' placeholder="Find the corner points of a feasible region"' : ""}></label>
        ${node?.origin === "notes" ? '<p class="field-hint">This entry came from the lecture notes. Once you rename it, a later edition of the notes no longer renames it.</p>' : ""}
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${node ? "Save" : "Add"}</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (dialog().querySelector("#question-bank-node"));
    const requestId = newId("bank-node");
    form.onsubmit = (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const body = { request_id: requestId, title: String(data.get("title") || ""), ...(numbered ? { number: String(data.get("number") || "") } : {}) };
      void sending(form, async (current) => {
        const view = node
          ? await api(route(`/nodes/${encodeURIComponent(node.id)}`), "POST", { ...body, based_on: node.revision })
          : await api(route(`/definitions/${encodeURIComponent(state.definitionId)}/nodes`), "POST", { ...body, level: kind, parent_id: parentId || null });
        if (current() && parentId) state.open.add(parentId);
        await landed(view, current);
        toast(node ? "Renamed." : "Added to the outline.");
      }, "Saving…");
    };
  }

  async function retireNode(/** @type {string} */ id, /** @type {boolean} */ retire) {
    const node = nodeMap().get(id);
    if (!node) return;
    const view = await post(`/nodes/${encodeURIComponent(id)}`, { request_id: newId("bank-node"), based_on: node.revision, retired: retire }, "");
    if (!view) return;
    // Reversible, so no confirmation first: the toast offers Restore. Questions keep their tags either way.
    if (retire) toast(`${nodeName(node, { long: true })} is retired: no longer offered for tags.`, { action: { label: "Restore", run: () => { void retireNode(id, false); } } });
    else toast("Restored to the outline.");
  }

  // -- PDFs ----------------------------------------------------------------------------------------------------------------------
  function openUpload() {
    if (!state?.view) return;
    if (!state.view.model.ready) { toast(state.view.model.problem || "Choose a Rubric drafting and review model in Compute settings first.", true); return; }
    const requestId = newId("bank-pdf");
    showDialog(`<form id="question-bank-upload" novalidate>${dialogHeader("Read questions from a PDF", [state.code, "every term"].join(" · "))}
      <div class="dialog-body">
        <p>Choose an old exam, quiz, worksheet or answer key. The model reads every problem out of it; you then choose which to add to the bank.</p>
        <label class="field">PDF<input type="file" name="file" accept="application/pdf,.pdf" required><span class="field-hint">Up to ${Math.floor(state.view.max_bytes / 1048576)} MB and 100 pages. From Word, choose File → Save As → PDF.</span></label>
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Upload and read</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (dialog().querySelector("#question-bank-upload"));
    const input = /** @type {HTMLInputElement} */ (form.elements.namedItem("file"));
    form.onsubmit = (event) => {
      event.preventDefault();
      const file = input.files?.[0];
      if (!file) { fail(form, "Choose the PDF."); return; }
      if (file.size > state.view.max_bytes) { fail(form, `That file is larger than ${Math.floor(state.view.max_bytes / 1048576)} MB, the most the PDF reader opens. Save a smaller copy (a lower scan resolution), then upload it.`); return; }
      const data = new FormData();
      data.append("request_id", requestId);
      data.append("file", file, file.name);
      void sending(form, async (current) => {
        const view = await api(route(`/definitions/${encodeURIComponent(state.definitionId)}/uploads`), "POST", data, {}, LONG);
        await landed(view, current);
        toast(view.repeated ? "This PDF was uploaded before; it is listed under PDFs read for questions." : "The model is reading the questions. This page shows them when it is done.");
      }, "Uploading…");
    };
  }

  /** How a section a PDF printed is shown: "Section 2.1" for a number, otherwise its heading ("Chapter 1 Review"). */
  function sectionLabel(/** @type {string} */ section) {
    const text = String(section || "").trim();
    const number = /^(?:section\s*)?(\d+(?:\.\d+)+)$/i.exec(text);
    return number ? `Section ${number[1]}` : text;
  }

  /**
   * Draw each figure the reading boxed (a canvas per figure) from its kept page picture once it scrolls near the view: the box widened a
   * little, as the picture will be cut when the problem is added.
   * @param {HTMLElement} root @param {string} uploadId
   */
  function drawFigures(root, uploadId) {
    /** @type {Map<number, Promise<HTMLImageElement>>} */ const pages = new Map();
    const load = (/** @type {number} */ page) => {
      let loading = pages.get(page);
      if (!loading) {
        const image = new Image();
        image.decoding = "async";
        image.src = route(`/uploads/${encodeURIComponent(uploadId)}/pages/${page}.jpg`);
        loading = image.decode().then(() => image);
        pages.set(page, loading);
      }
      return loading;
    };
    const draw = async (/** @type {HTMLCanvasElement} */ canvas) => {
      const figure = JSON.parse(canvas.dataset.bankFigure || "{}");
      try {
        const image = await load(figure.page);
        const pad = 0.012, w = image.naturalWidth, h = image.naturalHeight;
        const left = Math.max(0, figure.left - pad) * w, top = Math.max(0, figure.top - pad) * h;
        const width = Math.min(w, (figure.right + pad) * w) - left, height = Math.min(h, (figure.bottom + pad) * h) - top;
        const scale = Math.min(1, 360 / width);
        canvas.width = Math.max(1, Math.round(width * scale));
        canvas.height = Math.max(1, Math.round(height * scale));
        canvas.getContext("2d")?.drawImage(image, left, top, width, height, 0, 0, canvas.width, canvas.height);
      } catch {
        const note = document.createElement("span");
        note.className = "bank-muted";
        note.textContent = "The page could not be shown.";
        canvas.replaceWith(note);
      }
    };
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        observer.unobserve(entry.target);
        void draw(/** @type {HTMLCanvasElement} */ (entry.target));
      }
    }, { rootMargin: "300px 0px" });
    for (const canvas of root.querySelectorAll("canvas[data-bank-figure]")) observer.observe(canvas);
  }

  /**
   * Add every question a PDF's reading found, without reviewing them one by one (requirement, 2026-10-03). What the reading asked to check stays in each question's notes and
   * Show → Something to check finds them.
   * @param {string} uploadId
   */
  async function openAddAll(uploadId) {
    const current = captureDialogIntent(dialog());
    let found;
    try {
      found = await api(route(`/uploads/${encodeURIComponent(uploadId)}/found`));
      if (!current()) return;
    } catch (cause) {
      toast(message(cause), true);
      return;
    }
    /** @type {any[]} */ const waiting = found.questions.filter((/** @type {any} */ q) => !q.question_id);
    if (!waiting.length) { toast("Every question this PDF holds is in the bank already."); return; }
    const elsewhere = found.questions.filter((/** @type {any} */ q) => q.elsewhere).length;
    const toCheck = waiting.filter((q) => q.check?.length).length;
    const figures = waiting.reduce((n, q) => n + (q.figures?.length || 0), 0);
    const sections = new Set(waiting.map((q) => q.section).filter(Boolean)).size;
    const facts = [sections ? plural(sections, "section") : "", figures ? `${plural(figures, "graph")} saved as ${figures === 1 ? "a picture" : "pictures"}` : ""].filter(Boolean);
    showDialog(`<form id="question-bank-add-all" class="bank-dialog" novalidate>${dialogHeader(`Add all ${plural(waiting.length, "question")}`, e(found.source_name))}
      <div class="dialog-body"><p>Adds every question the model found that is not in the bank yet${facts.length ? ` (${e(facts.join("; "))})` : ""}, without reviewing them one by one. The model tags them within a few minutes.</p>${elsewhere ? `<p class="field-hint">${e(plural(elsewhere, "other question"))} it holds ${elsewhere === 1 ? "is" : "are"} in the bank already, from grading or another file, and ${elsewhere === 1 ? "is" : "are"} left out.</p>` : ""}
        ${toCheck ? calloutMarkup({ tone: "warning", title: `${plural(toCheck, "question")} with something to check`, body: "What to check stays in each one's notes. To see them later, choose Show → Something to check in the bank." }) : ""}
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Add all ${plural(waiting.length, "question")}</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (dialog().querySelector("#question-bank-add-all"));
    const requestId = newId("bank-accept-all");
    form.onsubmit = (event) => {
      event.preventDefault();
      void sending(form, async (current) => {
        let view = null, pictures = 0, missed = 0;
        const indices = waiting.map((q) => q.index);
        for (let start = 0; start < indices.length; start += ADD_AT_ONCE) {
          view = await api(route(`/uploads/${encodeURIComponent(uploadId)}/accept`), "POST",
            { request_id: `${requestId}:${start / ADD_AT_ONCE}`, run_id: found.run_id, indices: indices.slice(start, start + ADD_AT_ONCE) }, {}, LONG);
          pictures += view.pictures || 0;
          missed += view.pictures_missed || 0;
        }
        await landed(view, current);
        toast(`Added ${plural(indices.length, "question")}${pictures ? ` and ${plural(pictures, "picture")}` : ""}. The model tags them within a few minutes.`);
        if (missed) toast(`${plural(missed, "picture")} could not be cut: cut them from the PDF in each question.`, true);
      }, "Adding…");
    };
  }

  async function openReview(/** @type {string} */ uploadId) {
    const current = captureDialogIntent(dialog());
    let found;
    try {
      found = await api(route(`/uploads/${encodeURIComponent(uploadId)}/found`));
      if (!current()) return;
    } catch (cause) {
      toast(message(cause), true);
      return;
    }
    /** @type {any[]} */ const items = found.questions;
    const waiting = items.filter((q) => !q.question_id).length;
    const toCheck = items.filter((q) => !q.question_id && q.check?.length).length;
    const sectioned = items.some((q) => q.section);
    /** @type {{name: string, items: any[]}[]} */ const groups = [];
    for (const q of items) {
      const name = sectioned ? q.section || "" : "";
      if (!groups.length || groups[groups.length - 1].name !== name) groups.push({ name, items: [] });
      groups[groups.length - 1].items.push(q);
    }
    const row = (/** @type {any} */ q) => {
      const added = !q.question_id ? "" : q.elsewhere ? `<span class="chip" data-tone="success" title="Already in the bank, from grading or another file">In the bank as ${e(q.elsewhere)}</span>`
        : `<span class="chip" data-tone="success">${q.earlier ? "Added from an earlier reading" : "In the bank"}</span>`;
      const flag = !q.question_id && q.check?.length ? '<span class="chip" data-tone="warning">Check</span>' : "";
      const checks = q.check?.length ? `<ul class="bank-found-checks" aria-label="Check">${q.check.map((/** @type {string} */ c) => `<li>${mathText(c, h)}</li>`).join("")}</ul>` : "";
      const figures = q.figures?.length ? `<div class="bank-found-figures">${q.figures.map((/** @type {any} */ f) => `<canvas class="bank-found-figure" role="img" aria-label="${e(f.description || "A figure of this problem")}" data-bank-figure="${e(JSON.stringify({ page: f.page, ...f.region }))}" width="1" height="1"></canvas>`).join("")}<span class="field-hint">Added as ${q.figures.length === 1 ? "its picture" : "its pictures"}, cut from page ${e([...new Set(q.figures.map((/** @type {any} */ f) => f.page))].join(", "))}.</span></div>` : "";
      return `<li class="bank-found"${q.question_id ? ' data-added="true"' : ""}${q.check?.length ? ' data-check="true"' : ""}>
      <label class="bank-found-pick"><input type="checkbox" name="pick" value="${q.index}"${q.question_id ? " disabled" : " checked"}><span>${[q.content.number ? `Problem ${e(q.content.number)}` : "", `page ${e(q.page)}`].filter(Boolean).join(" · ")}</span>${added}${flag}</label>
      <div class="bank-found-body"><p class="bank-facts">${[state.view.vocabulary.kinds[q.content.kind], q.content.points !== null ? `${q.content.points} pts` : ""].filter(Boolean).map((f) => `<span class="chip" data-tone="neutral">${e(f)}</span>`).join("")}</p>${contentMarkup(q.content)}${checks}${figures}${q.content.notes ? `<p class="bank-notes">${mathText(q.content.notes, h)}</p>` : ""}</div></li>`;
    };
    const sections = groups.map((g, i) => {
      const open = g.items.filter((q) => !q.question_id).length;
      const head = sectioned ? `<header class="bank-found-head"><label class="bank-found-pick"><input type="checkbox" data-bank-section="${i}"${open ? " checked" : " disabled"}><span>${e(sectionLabel(g.name) || "Before the first section")}</span></label><span class="bank-muted">${plural(g.items.length, "problem")}${open < g.items.length ? ` · ${g.items.length - open} in the bank` : ""}</span></header>` : "";
      return `<section class="bank-found-section" data-bank-group="${i}">${head}<ul class="bank-found-list">${g.items.map(row).join("")}</ul></section>`;
    }).join("");
    const notes = found.notes.length ? disclosureMarkup({ label: "About the whole PDF", hint: found.notes.length, open: true, body: `<ul>${found.notes.map((/** @type {string} */ n) => `<li>${e(n)}</li>`).join("")}</ul>` }) : "";
    const older = found.rule && found.rule !== "bank-extract-v2" ? calloutMarkup({ tone: "info", title: "Read before the reader kept sections and figures",
      body: "Choose Read again to get each problem's section, its graphs as pictures and what to check in it; problems added already stay in the bank." }) : "";
    const only = toCheck ? `<label class="bank-found-filter"><input type="checkbox" data-bank-only-check> Only the ${plural(toCheck, "problem")} to check</label>` : "";
    showDialog(`<form id="question-bank-review" class="bank-dialog" novalidate>${dialogHeader("Questions found", `${e(found.source_name)} · ${plural(found.pages, "page")}`)}
      <div class="dialog-body">${older}${items.length ? `<p class="field-hint">Check each against the PDF; the model transcribed it and solved nothing. Untick what should not be kept.</p>${notes}
        <fieldset class="bank-fieldset"><legend>These pages are printed as</legend><div class="bank-row">
          <label class="field">Assignment<select name="printed_assignment"><option value="">Not an assessment in Faculty</option>${state.view.assignments.map((/** @type {any} */ a) => `<option value="${e(a.id)}">${e(a.title)}</option>`).join("")}</select></label>
          <label class="field">Version<select name="printed_version"><option value="">None</option>${LETTERS.map((l) => `<option value="${l}">${l}</option>`).join("")}</select></label></div>
          <span class="field-hint">Each problem is then linked to its printed number there: its grades are found and its versions linked.</span></fieldset>
        ${only}${sections}` : `${notes}<p>The model found no questions in this PDF.</p>`}
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><a class="button" ${viewLink(state.view.uploads.find((/** @type {any} */ u) => u.id === uploadId)?.url || "#", "PDF", e)}>Open the PDF</a><button type="button" class="button" data-bank-read-again title="Read every page again with the current reader">Read again</button><button type="button" class="button" data-action="close">Close</button>${waiting ? `<button type="submit" class="button primary" data-bank-add-count>Add ${plural(waiting, "question")}</button>` : ""}</footer></form>`);
    const form = /** @type {HTMLFormElement} */ (dialog().querySelector("#question-bank-review"));
    renderMath(form);
    drawFigures(form, uploadId);
    const picks = (/** @type {Element} */ scope) => /** @type {HTMLInputElement[]} */ ([...scope.querySelectorAll('input[name="pick"]:not(:disabled)')]);
    const count = () => {
      const picked = form.querySelectorAll('input[name="pick"]:checked').length;
      const button = /** @type {HTMLButtonElement|null} */ (form.querySelector("[data-bank-add-count]"));
      if (button) { button.textContent = `Add ${plural(picked, "question")}`; button.disabled = !picked; }
      for (const group of form.querySelectorAll("[data-bank-group]")) {
        const box = /** @type {HTMLInputElement|null} */ (group.querySelector("[data-bank-section]"));
        const mine = picks(group);
        if (!box || !mine.length) continue;
        const ticked = mine.filter((input) => input.checked).length;
        box.checked = ticked === mine.length;
        box.indeterminate = ticked > 0 && ticked < mine.length;
      }
    };
    form.onchange = (event) => {
      const target = /** @type {HTMLInputElement} */ (event.target);
      if (target.hasAttribute("data-bank-section")) {
        const group = target.closest("[data-bank-group]");
        if (group) for (const input of picks(group)) input.checked = target.checked;
      } else if (target.hasAttribute("data-bank-only-check")) {
        for (const item of form.querySelectorAll(".bank-found")) (/** @type {HTMLElement} */ (item)).hidden = target.checked && !item.hasAttribute("data-check");
        for (const group of form.querySelectorAll("[data-bank-group]")) (/** @type {HTMLElement} */ (group)).hidden = target.checked && !group.querySelector(".bank-found[data-check]");
      }
      count();
    };
    form.onclick = (event) => {
      if (!(/** @type {HTMLElement} */ (event.target)).closest("[data-bank-read-again]")) return;
      dialog().close();
      void post(`/uploads/${encodeURIComponent(uploadId)}/read`, { request_id: newId("bank-read") }, "Reading the PDF again. Problems added already are marked when it is done.");
    };
    const requestId = newId("bank-accept");
    form.onsubmit = (event) => {
      event.preventDefault();
      const indices = [...form.querySelectorAll('input[name="pick"]:checked')].map((input) => Number(/** @type {HTMLInputElement} */ (input).value));
      if (!indices.length) { fail(form, "Choose the questions to add."); return; }
      void sending(form, async (current) => {
        const data = new FormData(form);
        const printed = data.get("printed_assignment") ? { assignment_id: String(data.get("printed_assignment")), exam_version: String(data.get("printed_version") || "") } : null;
        const view = await api(route(`/uploads/${encodeURIComponent(uploadId)}/accept`), "POST", { request_id: requestId, run_id: found.run_id, indices, ...(printed ? { printed } : {}) }, {}, LONG);
        await landed(view, current);
        toast(`Added ${plural(indices.length, "question")}${view.pictures ? ` and ${plural(view.pictures, "picture")}` : ""}. The model tags them within a minute.`);
        if (view.pictures_missed) toast(`${plural(view.pictures_missed, "picture")} could not be cut: cut them from the PDF in each question.`, true);
      }, "Adding…");
    };
  }

  /**
   * The course Home's Question bank line: how many questions it holds and how many are tagged.
   * @param {any} group @param {HTMLElement} node
   */
  async function summary(group, node) {
    if (!group?.definition_id || !node) return;
    try {
      const view = await api(route(`/definitions/${encodeURIComponent(group.definition_id)}?summary=1`));
      if (!node.isConnected) return;
      node.textContent = view.questions ? `${plural(view.questions, "question")} · ${view.tagged} tagged` : "No questions yet";
    } catch {
      if (node.isConnected) node.textContent = "";
    }
  }

  return { render, summary };
}
