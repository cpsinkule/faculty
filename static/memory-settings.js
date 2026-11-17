// @ts-check
/**
 * The Memory page, `#memory` (ASSISTANT-MEMORY "ONE PAGE"; requirement, 2026-11-10). Opened from Workspace settings. One place for everything Faculty remembers:
 *
 *   Learning       a switch for each way Faculty learns by itself (chat, corrections of follow-up cards, rubric passes).
 *   How much…      the numbers behind what a request is told, each with its measured value one click away.
 *   The Assistant  every Assistant memory as one row: "With every request" and what it applies to change at once, on the row.
 *   Rubric         every course's rubric memories (rubric-memories.js draws them into its host here).
 *
 * The server decides everything (assistant/memory.py, grading/rubric_memory.py); this module draws its views and names the
 * controls. The Saved tab and a course's Home link here.
 */
import { bindFilterChips, disclosureMarkup, emptyMarkup, errorMessage, filterChipsMarkup, loadingMarkup, plural, runFormAction, setBusy } from "./ui.js";
import { patchChildren } from "./dom-patch.js";
import { esc } from "./assistant-markdown.js";
import { viewLink } from "./file-viewer.js";

const BASE = "/api/assistant";
const SWITCHES = /** @type {[string, string, string][]} */ ([
  ["capture", "Keep what I type in chat", "A lasting preference or rule in a chat message is remembered, and the chat says so with Undo."],
  ["learning", "Learn from my corrections of follow-up cards", "A reply you edit, a decision you change or a round you ask for again can teach a rule."],
  ["rubric_learning", "Learn rubric memories by itself", "After you certify an assignment or approve rubrics whose questions you answered. Off, “Learn from certified assignments” still works."],
]);
const NUMBERS = /** @type {[string, string, string, number][]} */ ([
  ["given", "Most memories for one request", "", 1],
  ["use_gate", "How strongly a memory must bear on a request", "0.30 to 0.90; higher tells a request fewer", 0.05],
  ["always_chars", "Room for “With every request” memories", "characters", 100],
]);
const FILTERS = [{ value: "all", label: "All" }, { value: "always", label: "With every request" }, { value: "own", label: "Yours" }, { value: "learned", label: "Written by the Assistant" }];
const SOURCE = /** @type {Record<string, string>} */ ({ chat: "from a chat message", form: "written here", followup: "from a follow-up", recording: "said in a recording" });
const day = (/** @type {string} */ stamp) => String(stamp || "").slice(0, 10);
const shown = (/** @type {number} */ value) => (Number.isInteger(value) ? String(value) : value.toFixed(2));

/** The fields the server takes for a memory's "Applies to". @param {string} owner */
export function ownerFields(owner) {
  const [scope, id] = String(owner || "workspace").split(":");
  return scope === "course" ? { scope, definition_id: id } : scope === "term" ? { scope, term_id: id } : { scope: "workspace" };
}

/** @param {any} memory */
const ownerOf = (memory) => (memory.scope === "course" ? `course:${memory.definition_id}` : memory.scope === "term" ? `term:${memory.term_id}` : "workspace");

/** @param {any} memory @param {any} listing @param {string} attribute */
function ownerSelect(memory, listing, attribute) {
  const value = ownerOf(memory);
  const options = [["workspace", "Everything"], ...(listing.courses || []).map((/** @type {any} */ c) => [`course:${c.id}`, `${c.label}, every term`]),
    ...(listing.terms || []).map((/** @type {any} */ t) => [`term:${t.id}`, `${t.label} only`])];
  // A memory of a past term keeps its owner in the list, so changing something else never moves it.
  if (!options.some(([key]) => key === value)) options.push([value, memory.applies_to]);
  return `<select ${attribute} aria-label="Applies to">${options.map(([key, label]) => `<option value="${esc(key)}"${key === value ? " selected" : ""}>${esc(label)}</option>`).join("")}</select>`;
}

/** Whose words a memory's are, for the filter and the row's line. @param {any} memory */
const written = (memory) => Boolean(memory.learned || memory.ruled || memory.typed);

/** @param {any} memory */
function factsLine(memory) {
  const given = memory.given;
  return [`Kept ${day(memory.created_at)}`, SOURCE[memory.source] || "",
    memory.learned ? "written by the Assistant from your correction" : memory.ruled ? "written by the Assistant, without the student"
      : memory.typed ? `reworded by the Assistant from “${String(memory.typed).replace(/\s+/g, " ").trim()}”` : "",
    memory.standalone === false ? "it may not read well on its own" : "",
    memory.last_used ? `last chosen ${day(memory.last_used)}` : "",
    given ? `given to ${plural(given.drafts, "draft")}${given.again ? `, ${given.again} still corrected this way` : ""}` : ""].filter(Boolean).join(" · ");
}

/** @param {any} memory */
function pictures(memory) {
  const kept = memory.pictures || [];
  return kept.length ? `<span class="memory-pictures">${kept.map((/** @type {any} */ picture, /** @type {number} */ index) => `<a ${viewLink(picture.route, `Remembered picture ${index + 1}`, esc)}><img src="${esc(picture.route)}" alt="A picture kept with this memory" loading="lazy" width="72"></a>`).join("")}</span>` : "";
}

/**
 * One standing Assistant memory: its words, then its controls on the same row. The tick and "Applies to" save as they change.
 * @param {any} memory @param {any} listing @param {boolean} editing
 */
export function memoryRowMarkup(memory, listing, editing = false) {
  const id = esc(memory.id);
  const words = editing
    ? `<form class="memory-edit" data-memory-words="${id}"><textarea name="content" rows="3" maxlength="4000" required aria-label="Remember">${esc(memory.content || "")}</textarea>
        <p class="form-error" role="alert" hidden></p><div class="memory-actions"><button class="button primary" type="submit">Save</button><button class="button" type="button" data-memory-cancel>Cancel</button></div></form>`
    : `<p class="memory-words">${esc(memory.content || "")}</p>`;
  return `<li class="memory-row" data-patch-key="memory-${id}" data-memory-row="${id}">
    ${words}<p class="memory-facts">${esc(factsLine(memory))}</p>${pictures(memory)}
    <div class="memory-controls"><label class="checkbox-field"><input type="checkbox" data-memory-always="${id}"${memory.always ? " checked" : ""}>With every request</label>
      ${ownerSelect(memory, listing, `data-memory-owner="${id}"`)}
      ${editing ? "" : `<button class="text-button" type="button" data-memory-edit="${id}">Edit</button>`}<button class="text-button danger" type="button" data-memory-remove="${id}">Remove</button></div>
  </li>`;
}

/** The switches and the numbers. @param {any} settings */
export function settingsMarkup(settings) {
  const switches = SWITCHES.map(([name, label, hint]) => `<div class="memory-switch"><label class="checkbox-field"><input type="checkbox" data-memory-switch="${name}"${settings[name] ? " checked" : ""}>${esc(label)}</label><p class="field-hint">${esc(hint)}</p></div>`).join("");
  const numbers = NUMBERS.map(([name, label, hint, step]) => {
    const [low, high] = settings.limits[name];
    const measured = settings.measured[name];
    return `<label class="field">${esc(label)}<input type="number" name="${name}" min="${low}" max="${high}" step="${step}" value="${esc(shown(settings[name]))}" required>
      <span class="field-hint">${esc([hint, `measured: ${shown(measured)}`].filter(Boolean).join(" · "))}</span></label>`;
  }).join("");
  const changed = NUMBERS.some(([name]) => settings[name] !== settings.measured[name]);
  return `<section class="memory-section" data-patch-key="learning" aria-labelledby="memory-learning"><div class="memory-head"><h3 id="memory-learning">How Faculty learns</h3></div>
      <div class="memory-switches">${switches}</div></section>
    <section class="memory-section" data-patch-key="limits" aria-labelledby="memory-limits"><div class="memory-head"><h3 id="memory-limits">How much a request is told</h3></div>
      <p class="memory-why">A request is given the memories that bear on it and the ones marked “With every request”.</p>
      <form class="memory-limits" data-memory-limits><div class="memory-numbers">${numbers}</div><p class="form-error" role="alert" hidden></p>
        <div class="memory-actions"><button class="button" type="submit">Save</button>${changed ? '<button class="text-button" type="button" data-memory-measured>Use the measured values</button>' : ""}</div></form></section>`;
}

/**
 * The Assistant's memories: the filter, a row each, the form that adds one, and what was replaced or removed.
 * @param {any} listing `GET /api/assistant/memory` @param {{filter?: string, editing?: string}} [view]
 */
export function memoriesMarkup(listing, { filter = "all", editing = "" } = {}) {
  const all = listing.memories || [];
  const standing = all.filter((/** @type {any} */ m) => m.status === "current");
  const gone = all.filter((/** @type {any} */ m) => m.status !== "current");
  const kept = standing.filter((/** @type {any} */ m) => filter === "always" ? m.always : filter === "own" ? !written(m) : filter === "learned" ? written(m) : true);
  // What goes with every request leads; the rest as the server lists them, newest first.
  const rows = [...kept.filter((/** @type {any} */ m) => m.always), ...kept.filter((/** @type {any} */ m) => !m.always)];
  const blank = { id: "", scope: "workspace" };
  const add = disclosureMarkup({ label: "Add a memory", key: "memory:add", className: "memory-add", body: `<form class="memory-edit" data-memory-new>
      <textarea name="content" rows="3" maxlength="4000" required aria-label="Remember"></textarea>
      <div class="memory-controls">${ownerSelect(blank, listing, 'name="owner"')}<label class="checkbox-field"><input type="checkbox" name="always">With every request</label></div>
      <p class="form-error" role="alert" hidden></p><div class="memory-actions"><button class="button primary" type="submit">Add memory</button></div></form>` });
  const removed = gone.length ? disclosureMarkup({ label: "Replaced and removed", hint: gone.length, key: "memory:gone", className: "memory-gone",
    body: `<ul class="memory-list" data-memory-list="assistant-gone">${gone.map((/** @type {any} */ m) => `<li class="memory-row" data-gone data-patch-key="gone-${esc(m.id)}" data-memory-row="${esc(m.id)}">
        <p class="memory-words">${esc(m.content || "")}</p><p class="memory-facts">${esc(`${m.status === "superseded" ? "Replaced" : m.reason === "undone" ? "Taken back" : "Removed"} ${day(m.updated_at)} · ${m.applies_to}`)}</p>${pictures(m)}
        <div class="memory-controls"><button class="text-button" type="button" data-memory-restore="${esc(m.id)}">Restore</button><button class="text-button danger" type="button" data-memory-erase="${esc(m.id)}">Erase its words${(m.pictures || []).length ? " and pictures" : ""}</button></div></li>`).join("")}</ul>` }) : "";
  const list = rows.length ? `<ul class="memory-list" data-memory-list="assistant">${rows.map((/** @type {any} */ m) => memoryRowMarkup(m, listing, m.id === editing)).join("")}</ul>`
    : `<p class="memory-why">${standing.length ? "None of this kind." : "Nothing is remembered yet. What you type to the Assistant is kept here, and you can add a memory yourself."}</p>`;
  return `<section class="memory-section" data-patch-key="assistant" aria-labelledby="memory-assistant">
      <div class="memory-head"><h3 id="memory-assistant" tabindex="-1">The Assistant’s memories <span class="memory-count">${standing.length}</span></h3>${filterChipsMarkup("memory-filter", FILTERS, filter, { label: "Which memories" })}</div>
      ${list}${add}${removed}</section>`;
}

/**
 * @param {{api: any, toast: any, rubricMemories: {render: (group: any, termLabel?: string, force?: boolean) => Promise<void> | void}, groups: () => any[]}} deps
 */
export function createMemorySettings({ api, toast, rubricMemories, groups }) {
  /** @type {{settings: any, listing: any} | null} */ let data = null;
  let filter = "all", editing = "", course = "", wanted = "";
  let generation = 0;
  /** @type {Promise<void> | null} */ let loading = null;
  let unbind = () => {};
  const root = () => /** @type {HTMLElement | null} */ (document.querySelector("#memory-section"));
  const part = (/** @type {string} */ name) => /** @type {HTMLElement | null} */ (root()?.querySelector(`[data-memory-part="${name}"]`));
  const changed = () => window.dispatchEvent(new CustomEvent("faculty:assistant-memory-changed"));

  /** The courses that have a course code: a course's rubric memories are one list for every term of it. */
  function courses() {
    /** @type {Map<string, any>} */ const found = new Map();
    for (const group of groups() || []) if (group.definition_id && !found.has(group.definition_id)) found.set(group.definition_id, group);
    return [...found.values()];
  }

  // A patch brings the markup to what the server holds, but a control the instructor touched keeps its own state (dom-patch.js):
  // a tick, a choice and, once saved, a number are set to what stands, so a refused change is seen to go back.
  function drawSettings(saved = false) {
    const node = part("settings");
    if (!node || !data) return;
    patchChildren(node, settingsMarkup(data.settings));
    for (const box of /** @type {NodeListOf<HTMLInputElement>} */ (node.querySelectorAll("[data-memory-switch]"))) box.checked = Boolean(data.settings[box.dataset.memorySwitch || ""]);
    if (saved) for (const field of /** @type {NodeListOf<HTMLInputElement>} */ (node.querySelectorAll("[data-memory-limits] input"))) field.value = shown(data.settings[field.name]);
  }
  function drawMemories(read = false) {
    const node = part("memories");
    if (!node || !data) return;
    patchChildren(node, memoriesMarkup(data.listing, { filter, editing }));
    for (const box of /** @type {NodeListOf<HTMLInputElement>} */ (node.querySelectorAll("[data-memory-always]"))) box.checked = Boolean(memory(box.dataset.memoryAlways || "")?.always);
    for (const choice of /** @type {NodeListOf<HTMLSelectElement>} */ (node.querySelectorAll("[data-memory-owner]"))) {
      const item = memory(choice.dataset.memoryOwner || "");
      if (item) choice.value = ownerOf(item);
    }
    if (!wanted) return;
    // A link asked for one memory ("reword it" on a chat's note, Change under "Memories given"), or for the form that adds one.
    const target = wanted === "new" ? node.querySelector("details.memory-add") : node.querySelector(`[data-memory-row="${CSS.escape(wanted)}"]`);
    // One kept a moment ago is not in what was read last: it is looked for again once this visit's read is in.
    if (!target && !read) return;
    wanted = "";
    if (!target) return;
    if (target instanceof HTMLDetailsElement) target.open = true;
    const gone = target.closest("details");
    if (gone instanceof HTMLDetailsElement) gone.open = true;
    target.scrollIntoView({ block: "center" });
    const words = target.querySelector("textarea");
    if (words) { words.focus({ preventScroll: true }); words.setSelectionRange(words.value.length, words.value.length); }
  }
  function drawRubric(fresh = false) {
    const node = part("rubric");
    if (!node) return;
    const found = courses();
    if (!found.some((group) => group.definition_id === course)) course = found[0]?.definition_id || "";
    const head = node.querySelector("[data-memory-courses]");
    if (head) patchChildren(head, found.length > 1 ? filterChipsMarkup("memory-course", found.map((group) => ({ value: group.definition_id, label: group.code })), course, { label: "Course" }) : found.length ? `<span class="memory-count">${esc(found[0].code)} · every term</span>` : "");
    const host = /** @type {HTMLElement | null} */ (node.querySelector("[data-rubric-memories-host]"));
    if (!host) return;
    const group = found.find((item) => item.definition_id === course);
    if (!group) { host.innerHTML = emptyMarkup({ icon: "book", title: "No course has a course code yet", body: "Rubric memories belong to a course code. Add one in a course’s settings first." }); return; }
    // A visit reads the course's memories again (a pass may have run since); the shell's second render joins that read.
    void rubricMemories.render(group, "", fresh);
  }

  async function read() {
    const [settings, listing] = await Promise.all([api(`${BASE}/memory-settings`), api(`${BASE}/memory`)]);
    return { settings, listing };
  }
  async function refresh() {
    const run = ++generation;
    const next = await read();
    if (run !== generation) return;
    data = next;
    drawSettings();
    drawMemories(true);
  }

  /** The shell renders a view and then again after its state read: an unforced render joins the read under way. */
  function render(force = false) {
    const node = root();
    if (!node) return Promise.resolve();
    const query = new URLSearchParams(location.hash.split("?")[1] || "");
    if (query.get("memory")) { wanted = query.get("memory") || ""; if (wanted !== "new") { filter = "all"; editing = wanted; } }
    if (query.get("course")) course = query.get("course") || "";
    const asked = Boolean(query.get("memory") || query.get("course"));
    if (asked) history.replaceState(history.state, "", "#memory");  // the link is taken once; a reload is the plain page
    if (!node.querySelector("[data-memory-part]")) {
      node.innerHTML = `<div class="memory-page"><div class="memory-parts" data-memory-part="settings">${loadingMarkup("Opening memory settings…", 3)}</div>
        <div class="memory-parts" data-memory-part="memories"></div>
        <section class="memory-section" data-memory-part="rubric" aria-labelledby="memory-rubric"><div class="memory-head"><h3 id="memory-rubric" tabindex="-1">Rubric memories</h3><span data-memory-courses></span></div>
          <p class="memory-why">What the rubric drafter and the rubric check follow, learned from your answers and from where graders stopped.</p>
          <section class="rubric-memories-host" data-rubric-memories-host aria-label="Rubric memories"></section></section></div>`;
      unbind();
      const filters = bindFilterChips(node, "memory-filter", (value) => { filter = value; editing = ""; drawMemories(); });
      const picked = bindFilterChips(node, "memory-course", (value) => { course = value; drawRubric(); });
      unbind = () => { filters(); picked(); };
      node.onclick = click;
      node.onchange = change;
      node.onsubmit = submit;
    }
    drawRubric(!loading);
    if (asked && query.get("course")) part("rubric")?.scrollIntoView({ block: "start" });
    if (data && !force) { drawSettings(); drawMemories(); }
    if (loading && !force) return loading;
    const promise = refresh().catch((cause) => {
      const settings = part("settings");
      if (!data && settings) settings.innerHTML = emptyMarkup({ icon: "info", title: "Memory settings could not be opened", body: errorMessage(cause), action: { label: "Try again", data: { memoryRetry: "" } } });
      else if (data) toast("Couldn’t refresh the memories. Showing the last loaded ones.", { tone: "warning" });
    }).finally(() => { if (loading === promise) loading = null; });
    loading = promise;
    return promise;
  }

  /** One memory as the server lists it now. @param {string} id */
  function memory(id) { return (data?.listing.memories || []).find((/** @type {any} */ item) => item.id === id); }

  /** A change of one memory that asks Jev nothing: its mark, or what it applies to. @param {any} item @param {Record<string, any>} fields */
  async function save(item, fields) {
    await api(`${BASE}/memories/${encodeURIComponent(item.id)}`, "POST", { content: item.content, ...ownerFields(ownerOf(item)), always: Boolean(item.always), revision: item.revision, ...fields });
    changed();
    await refresh();
  }

  async function change(/** @type {Event} */ event) {
    const control = /** @type {HTMLInputElement | HTMLSelectElement} */ (event.target);
    const name = control.getAttribute("data-memory-switch");
    try {
      if (name && data) {
        const on = /** @type {HTMLInputElement} */ (control).checked;
        data.settings = await api(`${BASE}/memory-settings`, "POST", { version: data.settings.version, [name]: on });
        drawSettings();
        toast(`${SWITCHES.find(([key]) => key === name)?.[1]}: ${on ? "on" : "off"}.`);
        return;
      }
      const always = control.getAttribute("data-memory-always"), owner = control.getAttribute("data-memory-owner");
      const item = memory(always || owner || "");
      if (!item) return;
      if (always) {
        const on = /** @type {HTMLInputElement} */ (control).checked;
        await save(item, { always: on });
        toast(on ? "Given with every request." : "Given when a request is about it.");
      } else if (owner) {
        await save(item, ownerFields(control.value));
        toast("Moved.");
      }
    } catch (cause) {
      toast(errorMessage(cause), true);
      // The control goes back to what the server holds.
      await refresh().catch(() => {});
      drawSettings();
      drawMemories();
    }
  }

  async function click(/** @type {MouseEvent} */ event) {
    const button = /** @type {HTMLButtonElement | null} */ ((/** @type {HTMLElement} */ (event.target)).closest("button"));
    if (!button || button.type === "submit" || button.disabled) return;
    const d = button.dataset;
    if ("memoryRetry" in d) { void render(true); return; }
    if (d.memoryEdit) { editing = d.memoryEdit; wanted = editing; drawMemories(); return; }
    if ("memoryCancel" in d) { editing = ""; drawMemories(); return; }
    if (!("memoryMeasured" in d) && !d.memoryRemove && !d.memoryRestore && !d.memoryErase) return;
    // Erasing blanks the words for good, so it is asked once more, in place.
    if (d.memoryErase && !d.confirmed) { button.dataset.confirmed = "1"; button.textContent = "Erase for good?"; return; }
    setBusy(button, true);
    try {
      if ("memoryMeasured" in d && data) {
        data.settings = await api(`${BASE}/memory-settings`, "POST", { version: data.settings.version, given: null, use_gate: null, always_chars: null });
        drawSettings(true);
        toast("Back to the measured values.");
        return;
      }
      if (d.memoryRemove) {
        const item = memory(d.memoryRemove);
        if (!item) return;
        await api(`${BASE}/memory/${encodeURIComponent(item.id)}/remove`, "POST", { revision: item.revision });
        toast("Removed. It is under Replaced and removed.", { action: { label: "Undo", run: async () => { await api(`${BASE}/memory/${encodeURIComponent(item.id)}/restore`, "POST", {}); changed(); await refresh(); } } });
      }
      if (d.memoryRestore) await api(`${BASE}/memory/${encodeURIComponent(d.memoryRestore)}/restore`, "POST", {});
      if (d.memoryErase) await api(`${BASE}/memory/${encodeURIComponent(d.memoryErase)}/erase`, "POST", {});
      changed();
      await refresh();
    } catch (cause) { toast(errorMessage(cause), true); }
    finally { setBusy(button, false); }
  }

  function submit(/** @type {SubmitEvent} */ event) {
    const form = /** @type {HTMLFormElement} */ (event.target);
    if (!form.matches("[data-memory-limits], [data-memory-new], [data-memory-words]")) return;
    event.preventDefault();
    void runFormAction(form, async () => {
      const values = Object.fromEntries(new FormData(form));
      if (form.matches("[data-memory-limits]")) {
        if (!data) return;
        // A number left at its measured value is sent as none of the instructor's own, so it follows the measured one.
        const measured = data.settings.measured;
        const own = Object.fromEntries(NUMBERS.map(([name]) => [name, Number(values[name]) === measured[name] ? null : Number(values[name])]));
        data.settings = await api(`${BASE}/memory-settings`, "POST", { version: data.settings.version, ...own });
        drawSettings(true);
        toast("Saved.");
        return;
      }
      if (form.matches("[data-memory-new]")) {
        // Your own wording is checked as a chat message's is: a statement naming a student or a record's fact is refused here, in place.
        await api(`${BASE}/memories`, "POST", { content: values.content, ...ownerFields(String(values.owner)), always: values.always === "on" });
        form.reset();
      } else {
        const item = memory(form.dataset.memoryWords || "");
        if (!item) return;
        await api(`${BASE}/memories/${encodeURIComponent(item.id)}`, "POST", { content: values.content, ...ownerFields(ownerOf(item)), always: Boolean(item.always), revision: item.revision });
        editing = "";
      }
      changed();
      await refresh();
    });
  }

  return { render };
}
