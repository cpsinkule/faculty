// PICKERS (ASSISTANT; requirement, 2026-10-09: the Assistant's box should help write the message): typed at the start of the box,
// "/" lists questions to start from (the page's starters, a few that fit anywhere, and this tab's own recent messages); typed
// before a name, "@" lists students and sections. Choosing only writes into the box: a "/" choice becomes the message, an "@"
// choice writes the name and remembers which record it stands for, so the message carries that record's IDs as hints (never
// its values). Nothing is sent until Send. Typing in the box is not a shortcut: these keys work only while the list is open.
import { esc } from "./assistant-markdown.js";
import { matchItems } from "./palette.js";
import { startersFor } from "./assistant-chat.js";

/** Questions that fit any page. */
const ANYWHERE = ["What needs me today?", "What looks wrong on this page?", "Summarize this page in five lines.", "What changed here since yesterday?"];
const SHOWN = 6;

/**
 * What is being picked at the caret, or null. "/" counts only as the box's first character, on its first line: the whole box is
 * replaced. "@" counts at the start or after a space (so an email address never opens it), up to 30 characters with no line
 * break: only that part is replaced.
 * @param {string} value @param {number} caret
 */
export function trigger(value, caret) {
  const before = String(value || "").slice(0, caret);
  if (before.startsWith("/") && !before.includes("\n")) return { kind: "/", query: before.slice(1), start: 0, end: String(value).length };
  const at = before.lastIndexOf("@");
  if (at < 0 || at > 0 && !/\s/.test(before[at - 1])) return null;
  const query = before.slice(at + 1);
  if (query.length > 30 || /[\n@]/.test(query) || query.startsWith(" ")) return null;
  return { kind: "@", query, start: at, end: caret };
}

/** The "/" list: the page's starters, the ones that fit anywhere, then the instructor's own recent messages. @param {string} hash @param {string[]} recent */
export function slashItems(hash, recent = []) {
  const page = startersFor(hash);
  const seen = new Set();
  const once = (/** @type {string} */ group) => (/** @type {string} */ words) => { const key = words.trim(); if (!key || seen.has(key)) return []; seen.add(key); return [{ group, label: key, detail: "", keywords: "", insert: key }]; };
  return [...page.flatMap(once("For this page")), ...ANYWHERE.flatMap(once("Anywhere")), ...recent.slice(0, 8).flatMap(once("Your recent messages"))];
}

/**
 * The "@" list from the students read (GET /api/student-notes/students, as the palette reads it): each current student, and
 * each section they are in. A choice carries the IDs that name it.
 * @param {any} source
 */
export function mentionItems(source) {
  const students = [], sections = new Map();
  for (const group of source?.groups || []) {
    for (const student of group.students || []) {
      const numbers = (student.sections || []).join(", ");
      students.push({ group: "Students", label: String(student.name || ""), detail: `${group.code} · ${group.term_label} · Section ${numbers}${student.dropped ? " · Dropped" : ""}`, keywords: "",
        insert: String(student.name || ""), record: { term_id: String(group.term_id || ""), section_id: String(student.section_id || ""), enrollment_id: String(student.enrollment_id || "") } });
      if (student.section_id && (student.sections || []).length === 1 && !sections.has(student.section_id)) {
        sections.set(student.section_id, { group: "Sections", label: `${group.code} section ${numbers}`, detail: String(group.term_label || ""), keywords: "",
          insert: `${group.code} section ${numbers}`, record: { term_id: String(group.term_id || ""), section_id: String(student.section_id) } });
      }
    }
  }
  return [...students, ...sections.values()];
}

/** The open list's markup: a label where a group starts, each choice with its detail. @param {any[]} items @param {number} active @param {string} id */
export function pickerMarkup(items, active, id) {
  let group = "";
  return items.map((item, index) => {
    const head = item.group !== group ? `<li class="assistant-picker-group" role="presentation">${esc(item.group)}</li>` : "";
    group = item.group;
    return `${head}<li role="option" id="${esc(id)}-${index}" data-pick="${index}" aria-selected="${index === active}"><span>${esc(item.label)}</span>${item.detail ? `<small>${esc(item.detail)}</small>` : ""}</li>`;
  }).join("");
}

/**
 * The record hints of the names still in a message: a later name of the same kind replaces an earlier one (a message carries
 * one student, one section).
 * @param {{text: string, record: Record<string, string>}[]} mentions @param {string} message
 */
export function mentionedRecord(mentions, message) {
  const record = /** @type {Record<string, string>} */ ({});
  for (const mention of mentions) if (mention.text && String(message || "").includes(mention.text)) Object.assign(record, Object.fromEntries(Object.entries(mention.record).filter(([, value]) => value)));
  return record;
}

/**
 * Gives one composer its pickers. `composer` is assistant-chat.js's createComposer (it hands the box's keys to the open list and
 * asks for the mentioned record when a message goes).
 * @param {{form: HTMLFormElement, field: HTMLTextAreaElement, composer: any, api: (path: string) => Promise<any>}} spec
 */
export function attachPickers({ form, field, composer, api }) {
  const list = document.createElement("ul");
  list.className = "assistant-picker";
  list.id = `${field.id}-picker`;
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", "Choices for your message");
  list.hidden = true;
  form.append(list);
  field.setAttribute("aria-autocomplete", "list");
  field.setAttribute("aria-controls", list.id);
  /** @type {ReturnType<typeof trigger>} */
  let found = null;
  /** @type {any[]} */
  let items = [];
  let active = 0, query = "";
  /** @type {any} */
  let students = null;
  let reading = false;
  /** @type {{text: string, record: Record<string, string>}[]} */
  let mentions = [];

  function close() {
    found = null;
    if (list.hidden) return;
    list.hidden = true; list.innerHTML = "";
    field.removeAttribute("aria-activedescendant");
  }
  function draw() {
    list.innerHTML = pickerMarkup(items, active, list.id);
    list.hidden = false;
    field.setAttribute("aria-activedescendant", `${list.id}-${active}`);
    list.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }
  function update() {
    found = trigger(field.value, field.selectionStart ?? field.value.length);
    if (!found) { close(); return; }
    if (found.kind === "@" && !students && !reading) {
      reading = true;
      void api("/api/student-notes/students").then((read) => { students = read; }, () => { students = { groups: [] }; }).finally(() => { reading = false; if (document.activeElement === field) update(); });
    }
    const all = found.kind === "/" ? slashItems(location.hash, composer.recent()) : mentionItems(students);
    items = matchItems(all, found.query).slice(0, SHOWN);
    if (!items.length) { list.hidden = true; list.innerHTML = ""; field.removeAttribute("aria-activedescendant"); return; }
    if (found.query !== query) { query = found.query; active = 0; }
    active = Math.min(active, items.length - 1);
    draw();
  }
  function choose(/** @type {number} */ index) {
    const item = items[index], at = found;
    if (!item || !at) return;
    const tail = at.kind === "@" ? " " : "";
    field.value = field.value.slice(0, at.start) + item.insert + tail + field.value.slice(at.end);
    const caret = at.start + item.insert.length + tail.length;
    field.setSelectionRange(caret, caret);
    if (item.record) mentions.push({ text: item.insert, record: item.record });
    close();
    composer.grow();
    field.focus();
  }
  /** The box's key while the list is open; true when the list took it. @param {KeyboardEvent} event */
  function handle(event) {
    if (list.hidden) return false;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      active = (active + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      draw();
      return true;
    }
    if ((event.key === "Enter" || event.key === "Tab") && !event.shiftKey && !event.isComposing) { event.preventDefault(); choose(active); return true; }
    // Escape closes the list, not the chat (FRONTEND "Keyboard").
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); return true; }
    return false;
  }
  // A press on a choice takes it; the box keeps the focus.
  list.addEventListener("mousedown", (event) => {
    const option = /** @type {HTMLElement | null} */ (/** @type {HTMLElement} */ (event.target).closest("[data-pick]"));
    if (!option) return;
    event.preventDefault();
    choose(Number(option.getAttribute("data-pick")));
  });
  field.addEventListener("click", update);
  field.addEventListener("keyup", (event) => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) update(); });
  composer.setPicker({ handle, update, close, open: () => !list.hidden, mentioned: (/** @type {string} */ message) => mentionedRecord(mentions, message), clear: () => { mentions = []; } });
}
