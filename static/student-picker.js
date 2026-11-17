// @ts-check
// Choosing one student among hundreds (requirement, 2026-09-24).
// A text box that filters as it is typed: every word typed must appear in an entry, in any order, ignoring case and accents, so
// "tran ella", "Elsa Tran" and "tran 507" all find "Tran, Elsa · section 507". Arrow keys move through the matches, Enter or a
// click chooses, Escape puts back what was chosen. Opening the box empties it, the current choice shown greyed as its placeholder
// (requirement, 2026-09-24: a click did not select the text, so it had to be deleted by hand before typing); leaving without a
// choice puts it back. The value lives in a hidden input, so a form reads it as it read a <select>.
// Typing four or more digits also asks the server (`search`) for the students whose UIN is closest (instructor, same day): UINs
// never reach the browser, only names, sections and a similarity. An ARIA combobox over a listbox; no inline styles (the
// Content Security Policy forbids them, so the list is placed through the CSSOM). It owns no data.

import { fold } from "./ui.js";

const SHOWN = 60;
const PLACEHOLDER = "Type a name, section or UIN…";

/**
 * @typedef {{value: string, text: string, note?: string}} PickerOption
 * @typedef {{label: string, options: PickerOption[]}} PickerGroup
 * @typedef {{id: string, name?: string, label: string, value: string, groups: PickerGroup[], locked?: boolean,
 *   search?: (digits: string) => Promise<PickerOption[]>}} PickerSpec
 */

/**
 * @param {(text: unknown) => string} escape
 * @param {PickerSpec} spec
 */
export function studentPicker(escape, spec) {
  const options = spec.groups.flatMap((group) => group.options.map((option) => ({...option, group: group.label})));
  const chosen = options.find((option) => option.value === spec.value);
  const id = escape(spec.id);
  const markup = `<div class="student-picker" data-student-picker="${id}"><input type="hidden" ${spec.name ? `name="${escape(spec.name)}"` : ""} value="${escape(spec.value)}" data-picker-value><input type="text" role="combobox" aria-label="${escape(spec.label)}" aria-autocomplete="list" aria-expanded="false" aria-controls="${id}-list" autocomplete="off" spellcheck="false" placeholder="${PLACEHOLDER}" value="${escape(chosen?.text || "")}" data-picker-text ${spec.locked ? "disabled data-locked" : ""}><ul id="${id}-list" class="student-picker-list" role="listbox" hidden></ul></div>`;
  return {
    markup,
    /** @param {ParentNode} root @param {(value: string) => unknown} [onChoose] */
    bind: (root, onChoose) => bind(root, spec, options, escape, onChoose),
  };
}

/**
 * Keep what is being typed in a picker across a re-render of `root` (the scan page redraws itself while work goes on).
 * @param {ParentNode} root
 * @returns {() => void} call after the new markup is bound
 */
export function keepPickerTyping(root) {
  const input = document.activeElement;
  if (!(input instanceof HTMLInputElement) || !input.matches("[data-picker-text]") || !root.contains(input)) return () => {};
  // Only while its list is open: a box just chosen from is done, and focusing it again would open its list again.
  if (input.getAttribute("aria-expanded") !== "true") return () => {};
  const id = input.closest("[data-student-picker]")?.getAttribute("data-student-picker") || "";
  const text = input.value, start = input.selectionStart, end = input.selectionEnd;
  return () => {
    const next = root.querySelector(`[data-student-picker="${CSS.escape(id)}"] [data-picker-text]`);
    if (!(next instanceof HTMLInputElement) || next.disabled) return;
    next.focus();
    next.value = text;
    next.setSelectionRange(start, end);
    next.dispatchEvent(new Event("input"));
  };
}

/**
 * @param {ParentNode} root
 * @param {PickerSpec} spec
 * @param {(PickerOption & {group: string})[]} options
 * @param {(text: unknown) => string} escape
 * @param {(value: string) => unknown} [onChoose]
 */
function bind(root, spec, options, escape, onChoose) {
  const box = root.querySelector(`[data-student-picker="${CSS.escape(spec.id)}"]`);
  if (!box) return;
  const value = /** @type {HTMLInputElement} */ (box.querySelector("[data-picker-value]"));
  const input = /** @type {HTMLInputElement} */ (box.querySelector("[data-picker-text]"));
  const list = /** @type {HTMLElement} */ (box.querySelector("[role=listbox]"));
  /** @type {(PickerOption & {group: string})[]} */
  let shown = [];
  let active = -1;
  // What the server found for the digits typed: kept only for those exact digits, and a late answer never replaces a newer one.
  /** @type {{digits: string, options: PickerOption[], pending: boolean, failed: boolean}} */
  let remote = {digits: "", options: [], pending: false, failed: false};
  let ticket = 0;
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let waiting;
  const digitsOf = (/** @type {string} */ text) => text.replace(/\D/g, "");
  // The roster is fixed for this binding: normalize each name once, rather than once per word on every keystroke.
  const searchable = options.map((option) => ({option, text: fold(`${option.text} ${option.note || ""}`)}));
  const labels = new Map();
  for (const option of options) if (!labels.has(option.value)) labels.set(option.value, option.text);
  const textOf = (/** @type {string} */ current) => labels.get(current) || "";

  const move = (/** @type {number} */ index) => {
    active = shown.length ? Math.max(0, Math.min(index, shown.length - 1)) : -1;
    list.querySelectorAll("[role=option]").forEach((node) => node.classList.toggle("active", Number(node.getAttribute("data-index")) === active));
    const current = active >= 0 ? list.querySelector(`[data-index="${active}"]`) : null;
    if (current) {
      input.setAttribute("aria-activedescendant", current.id);
      current.scrollIntoView({block: "nearest"});
    } else input.removeAttribute("aria-activedescendant");
  };
  // The list floats over whatever holds the picker (a scrolling table, a dialog), so it is placed against the window, beneath the
  // box or above it when there is more room there, and follows it while open.
  /** @type {AbortController | null} */
  let following = null;
  const place = () => {
    const rect = input.getBoundingClientRect(), below = innerHeight - rect.bottom, above = rect.top;
    const width = Math.min(Math.max(rect.width, 360), innerWidth - 8);  // wide enough for a name, its section and a note on one line
    list.style.left = `${Math.max(4, Math.min(rect.left, innerWidth - width - 4))}px`;
    list.style.width = `${width}px`;
    if (below < 220 && above > below) {
      list.style.top = "";
      list.style.bottom = `${innerHeight - rect.top + 2}px`;
      list.style.maxHeight = `${Math.min(288, above - 12)}px`;
    } else {
      list.style.bottom = "";
      list.style.top = `${rect.bottom + 2}px`;
      list.style.maxHeight = `${Math.min(288, below - 12)}px`;
    }
  };
  const close = () => {
    following?.abort();
    following = null;
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
    input.value = textOf(value.value);
    input.placeholder = PLACEHOLDER;
  };
  const paint = () => {
    // The text of the current choice shows everything; anything typed narrows it down.
    const query = input.value === textOf(value.value) ? "" : input.value;
    const words = fold(query).split(/[\s,·]+/).filter(Boolean);
    const local = searchable.filter(({text}) => words.every((word) => text.includes(word))).map(({option}) => option);
    const typed = digitsOf(query), asking = Boolean(spec.search) && typed.length >= 4;
    const found = asking && remote.digits === typed ? remote.options.map((option) => ({...option, group: "Closest UINs"})) : [];
    const matches = [...found, ...local.filter((option) => !found.some((match) => match.value === option.value))];
    shown = matches.slice(0, SHOWN);
    let group = "", html = "";
    shown.forEach((option, index) => {
      if (option.group && option.group !== group) html += `<li role="presentation" class="student-picker-group">${escape(option.group)}</li>`;
      group = option.group;
      html += `<li role="option" id="${escape(spec.id)}-option-${index}" data-index="${index}" data-group="${escape(option.group)}" aria-selected="${option.value === value.value}">${escape(option.text)}${option.note ? ` <small>${escape(option.note)}</small>` : ""}</li>`;
    });
    const lookup = asking && (remote.digits !== typed || remote.pending) ? `<li role="presentation" class="student-picker-empty">Looking up that UIN…</li>`
      : asking && remote.failed ? `<li role="presentation" class="student-picker-empty">The UIN could not be looked up.</li>` : "";
    html = lookup + html;
    // A UIN is looked up from four digits: fewer typed say so rather than "No one matches" (requirement, 2026-10-17).
    const short = Boolean(spec.search) && typed.length > 0 && typed.length < 4 && !/[^\d\s-]/.test(query);
    if (!matches.length && !lookup) html = short ? `<li role="presentation" class="student-picker-empty">Keep typing: a UIN is looked up from 4 digits.</li>`
      : `<li role="presentation" class="student-picker-empty">No one matches “${escape(query)}”.</li>`;
    else if (matches.length > SHOWN) html += `<li role="presentation" class="student-picker-empty">${matches.length - SHOWN} more: keep typing to narrow the list.</li>`;
    list.innerHTML = html;
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    place();
    if (!following) {
      following = new AbortController();
      addEventListener("scroll", place, {capture: true, passive: true, signal: following.signal});
      addEventListener("resize", place, {signal: following.signal});
    }
    move(query ? 0 : shown.findIndex((option) => option.value === value.value));
  };
  const choose = (/** @type {PickerOption | undefined} */ option) => {
    if (!option) return;
    const changed = option.value !== value.value;
    // A server search may find a student absent from the initial options; keep the chosen label while this picker lives.
    labels.set(option.value, option.text);
    value.value = option.value;
    close();
    if (changed) {
      value.dispatchEvent(new Event("change", {bubbles: true}));
      onChoose?.(option.value);
    }
  };

  // Typing starts in an empty box; what is chosen stays in sight as the placeholder until something else is chosen.
  const open = () => {
    input.placeholder = textOf(value.value) || PLACEHOLDER;
    input.value = "";
    paint();
  };
  input.addEventListener("focus", open);
  input.addEventListener("click", () => { if (list.hidden) open(); });  // clicked again after Escape closed the list
  const ask = () => {
    const typed = digitsOf(input.value === textOf(value.value) ? "" : input.value);
    clearTimeout(waiting);
    if (!spec.search || typed.length < 4 || (remote.digits === typed && !remote.failed)) return;
    waiting = setTimeout(() => {
      const mine = ++ticket;
      remote = {digits: typed, options: [], pending: true, failed: false};
      spec.search?.(typed).then((found) => {
        if (mine !== ticket) return;
        remote = {digits: typed, options: found, pending: false, failed: false};
        if (!list.hidden) paint();
      }, () => {
        if (mine !== ticket) return;
        remote = {digits: typed, options: [], pending: false, failed: true};
        if (!list.hidden) paint();
      });
    }, 180);
  };
  input.addEventListener("input", () => { active = 0; ask(); paint(); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (list.hidden) paint();
      move(active + (event.key === "ArrowDown" ? 1 : -1));
    } else if (event.key === "Enter") {
      // Never submits the surrounding form: Enter chooses the highlighted match.
      event.preventDefault();
      if (!list.hidden) choose(shown[active]);
    } else if (event.key === "Escape" && !list.hidden) {
      event.preventDefault();
      event.stopPropagation();  // Closes the list, not the dialog around it.
      close();
      input.select();
    } else if (event.key === "Tab") close();
  });
  list.addEventListener("mousedown", (event) => event.preventDefault());  // Keeps the focus in the text box.
  list.addEventListener("click", (event) => {
    const node = /** @type {HTMLElement} */ (event.target).closest("[role=option]");
    if (node) choose(shown[Number(node.getAttribute("data-index"))]);
  });
  box.addEventListener("focusout", (event) => {
    if (!box.contains(/** @type {Node | null} */ (event.relatedTarget))) close();
  });
}
