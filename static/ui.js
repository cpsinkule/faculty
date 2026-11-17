// @ts-check
import { icon as iconSvg } from "./icons.js";
import { campusToday } from "./calendar-feed.js";

/** @param {unknown} cause */
export function errorMessage(cause) {
  return cause instanceof Error ? cause.message : "Something went wrong. Please try again.";
}

/** The header Close a dialogHeader draws: the dialog's own close control, not a footer Cancel. @param {HTMLButtonElement} button */
function isDialogClose(button) {
  return button.dataset?.action === "close" && Boolean(button.closest?.(".dialog-header"));
}

/**
 * Prevent duplicate form submissions and retain intentionally disabled controls.
 * Each operation still owns validation, confirmation, and any retry identity.
 * With `keepClose`, the dialog's own header Close stays enabled while the operation runs, for an operation that only reads (a
 * Mail Review request): closing then leaves nothing half-done, and the caller ignores the late result with dialogSession. Every
 * other control stays disabled; a write keeps its dialog (the Escape key is held back the same way, see app.js).
 * @param {HTMLFormElement} form
 * @param {() => Promise<unknown>} operation
 * @param {{keepClose?: boolean}} [options]
 */
export async function runFormAction(form, operation, { keepClose = false } = {}) {
  if (form.dataset.saving === "true") return;
  const buttons = [...form.querySelectorAll("button")].filter((button) => !(keepClose && isDialogClose(button)));
  const disabled = buttons.map((button) => button.disabled);
  // A form can also contain an important warning with role=alert; leave it visible.
  const error = form.querySelector('.form-error[role="alert"], [data-form-error]');
  const previousBusy = form.getAttribute("aria-busy");
  if (error instanceof HTMLElement) { error.textContent = ""; error.hidden = true; }
  form.dataset.saving = "true";
  if (keepClose) form.dataset.closable = "true";  // Escape closes it too (app.js), like the header Close
  form.setAttribute("aria-busy", "true");
  buttons.forEach((button) => { button.disabled = true; });
  try {
    await operation();
  } catch (cause) {
    if (!(error instanceof HTMLElement)) throw cause;
    error.textContent = errorMessage(cause);
    error.hidden = false;
  } finally {
    form.dataset.saving = "false";
    delete form.dataset.closable;
    if (previousBusy === null) form.removeAttribute("aria-busy");
    else form.setAttribute("aria-busy", previousBusy);
    buttons.forEach((button, index) => { button.disabled = disabled[index]; });
  }
}

/**
 * Disable a control while a request it started is in flight, marked busy (the wait cursor), or give it back.
 * A control disabled for any other reason shows not-allowed: the instructor still has something to do.
 * @param {HTMLButtonElement | null | undefined} control
 * @param {boolean} busy
 */
export function setBusy(control, busy) {
  if (!control) return;
  control.disabled = busy;
  markBusy(control, busy);
}

/**
 * Mark an element busy or not without touching its disabled state (a dialog whose sync() derives it).
 * @param {Element} element
 * @param {boolean} busy
 */
export function markBusy(element, busy) {
  if (busy) element.setAttribute("aria-busy", "true");
  else element.removeAttribute("aria-busy");
}

/**
 * Capture the exact dialog content so late responses cannot paint a replacement
 * dialog. A closed and reopened dialog with different content has a new session.
 * @param {HTMLElement} host
 * @param {HTMLDialogElement} dialog
 */
export function dialogSession(host, dialog) {
  return () => host.isConnected && dialog.open && dialog.contains(host);
}

/**
 * The behavior of a programmatic scroll: instant under reduced motion, smooth otherwise. Read when the scroll happens.
 * @returns {ScrollBehavior}
 */
/** @type {WeakMap<HTMLDialogElement, {epoch: number, observer: MutationObserver | null}>} */
const dialogIntents = new WeakMap();
let intentNavigation = 0, watchingIntentNavigation = false, guardCountsHashChanges = false;

/**
 * Reserve an opening before reading its options. A newer opening, navigation,
 * or a change to the current dialog abandons this read without replacing the UI.
 * Use dialogSession after the new dialog has been drawn.
 * @param {HTMLDialogElement} dialog
 */
export function captureDialogIntent(dialog) {
  if (!watchingIntentNavigation && typeof window !== "undefined") {
    const navigated = () => { intentNavigation++; };
    // With the shell's leave guard installed, it counts an accepted hash change before any view hears of it (see
    // installLeaveGuard); a listener added here would hear it after the view had already captured its intent.
    if (!guardCountsHashChanges) window.addEventListener("hashchange", navigated);
    window.addEventListener("popstate", navigated);
    watchingIntentNavigation = true;
  }
  let state = dialogIntents.get(dialog);
  if (!state) {
    state = { epoch: 0, observer: null };
    const found = state;
    if (typeof MutationObserver !== "undefined") {
      found.observer = new MutationObserver((records) => { found.epoch += records.length; });
      found.observer.observe(dialog, { childList: true, attributes: true, attributeFilter: ["open"] });
    }
    dialogIntents.set(dialog, found);
  }
  state.epoch += state.observer?.takeRecords().length || 0;
  const token = ++state.epoch, navigation = intentNavigation;
  const route = globalThis.location?.href;
  const content = dialog.firstElementChild, open = dialog.open;
  return () => {
    state.epoch += state.observer?.takeRecords().length || 0;
    return state.epoch === token && intentNavigation === navigation && globalThis.location?.href === route
      && dialog.firstElementChild === content && dialog.open === open;
  };
}

/**
 * Rows are already escaped markup built by the caller; labels/headings are text.
 * @param {(value: string) => string} escape
 * @param {string[]} headings
 * @param {string[]} rows
 * @param {string} label
 */
export function tableMarkup(escape, headings, rows, label) {
  return `<div class="data-table" tabindex="0" role="region" aria-label="${escape(label)}"><table><thead><tr>${headings.map((heading) => `<th scope="col">${escape(heading)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}

/* ------------------------------------------------------------------------------------------------------------------
   Component vocabulary (UX overhaul, 2026-09-26). Markup helpers return strings: every piece of text given to them is
   escaped here, and only parameters named `html`/`body` in disclosureMarkup/aboutMarkup take caller-built markup.
   Styles live in components.css; FRONTEND.md "Component vocabulary" says when to use each one. */


/** @type {Record<string, string>} */
const ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
/** Escape text for HTML content or a quoted attribute. @param {unknown} value */
export function escape(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ENTITIES[char]);
}

/** Text lower-cased with its accents removed, for matching what is typed against names. @param {unknown} text */
export function fold(text) {
  return String(text ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

/**
 * A count and its noun: "1 copy", "3 copies". Plain digits, no thousands grouping.
 * @param {number} n
 * @param {string} one
 * @param {string} [many]
 */
export function plural(n, one, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

/** data-* attributes from {clearFilters: "", id: 3} → ' data-clear-filters="" data-id="3"'. @param {Record<string, unknown>} [data] */
function dataAttributes(data = {}) {
  return Object.entries(data).map(([key, value]) => ` data-${key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}="${escape(value)}"`).join("");
}

/**
 * One action: a link when it has href, otherwise a button carrying data-* attributes for the page's own handler.
 * @param {{label: string, href?: string, data?: Record<string, unknown>, primary?: boolean, className?: string}} action
 * @param {string} [extra] additional class names
 */
export function actionMarkup(action, extra = "") {
  const classes = ["button", action.primary ? "primary" : "", action.className || "", extra].filter(Boolean).join(" ");
  return action.href
    ? `<a class="${classes}" href="${escape(action.href)}">${escape(action.label)}</a>`
    : `<button type="button" class="${classes}"${dataAttributes(action.data)}>${escape(action.label)}</button>`;
}

const TONES = new Set(["info", "warning", "danger", "success", "neutral"]);
/**
 * A callout: one problem or notice, one sentence, at most one action. role=alert only for danger.
 * @param {{tone?: string, title: string, body?: string, action?: {label: string, href?: string, data?: Record<string, unknown>} | null, id?: string, className?: string}} spec
 */
export function calloutMarkup({ tone = "info", title, body = "", action = null, id = "", className = "" }) {
  const safeTone = TONES.has(tone) ? tone : "info";
  return `<div class="callout${className ? ` ${escape(className)}` : ""}"${id ? ` id="${escape(id)}"` : ""} data-tone="${safeTone}" role="${safeTone === "danger" ? "alert" : "status"}"><strong class="callout-title">${escape(title)}</strong>${body ? `<p>${escape(body)}</p>` : ""}${action ? actionMarkup(action, "callout-action") : ""}</div>`;
}

// WHO SEES A COMMENT (GRADING, requirement, 2026-11-11): the one wording for who reads what a field holds. `you`: kept in Faculty; `students`: sent to Gradescope with the grade;
// `student`: a reply to one student's request there.
export const SEEN_BY = { you: "Only you see this", students: "Students see this on Gradescope", student: "The student sees this on Gradescope" };
/** The mark inside a field's label, under its words. @param {keyof typeof SEEN_BY} who */
export function seenByMarkup(who) {
  return `<small class="field-hint" data-seen-by="${escape(who)}">${escape(SEEN_BY[who])}</small>`;
}

/**
 * Toast v2's arguments, normalised. Backward compatible: toast(message), toast(message, true) for an error, or
 * toast(message, {tone, action: {label, run}, persist, timeout}). Errors (tone danger) persist until dismissed; a toast
 * with an action (e.g. Restore) stays longer so the action can be reached.
 * @param {unknown} message
 * @param {boolean | {tone?: string, error?: boolean, action?: {label?: string, run?: Function} | null, persist?: boolean, timeout?: number}} [options]
 */
export function toastSpec(message, options = false) {
  const settings = options && typeof options === "object" ? options : { tone: options ? "danger" : "success" };
  const asked = settings.tone === "error" ? "danger" : settings.tone;  // 'error' is the danger tone, never a success toast
  const tone = TONES.has(String(asked)) && asked !== "neutral" ? String(asked) : settings.error ? "danger" : "success";
  const action = settings.action && settings.action.label && typeof settings.action.run === "function"
    ? { label: String(settings.action.label), run: settings.action.run } : null;
  const persist = typeof settings.persist === "boolean" ? settings.persist : tone === "danger";
  const timeout = persist ? 0 : Number(settings.timeout) > 0 ? Number(settings.timeout) : action ? 8000 : 4000;
  return { message: String(message ?? "").trim(), tone, action, persist, timeout, role: tone === "danger" ? "alert" : "status" };
}

const DISCLOSURE_PREFIX = "faculty:disclosure:";
/** The remembered open state of a keyed disclosure, or null when nothing is stored (or storage is unavailable). @param {string} key */
export function rememberedOpen(key) {
  try {
    const value = globalThis.localStorage?.getItem(DISCLOSURE_PREFIX + key);
    return value === "1" ? true : value === "0" ? false : null;
  } catch {
    return null;
  }
}

/**
 * A disclosure with the shared +/− marker. `body` is caller-built markup. A `key` makes the browser remember whether it
 * was left open (per viewer, rememberDisclosures() must be bound once by the shell); `open` is the default until then.
 * @param {{label: string, body: string, hint?: string | number, key?: string, open?: boolean, className?: string, id?: string}} spec
 */
export function disclosureMarkup({ label, body, hint = "", key = "", open = false, className = "", id = "" }) {
  const stored = key ? rememberedOpen(key) : null;
  const isOpen = stored ?? open;
  return `<details class="disclosure${className ? ` ${escape(className)}` : ""}"${id ? ` id="${escape(id)}"` : ""}${key ? ` data-disclosure-key="${escape(key)}"` : ""}${isOpen ? " open" : ""}><summary><span>${escape(label)}</span>${hint !== "" && hint !== null && hint !== undefined ? `<span class="disclosure-hint">${escape(hint)}</span>` : ""}</summary><div class="disclosure-body">${body}</div></details>`;
}

/**
 * 'About this page': the page's rationale and how-to, closed by default, its open state remembered per route.
 * @param {string} routeKey e.g. "inbox" or "grading/setup"
 * @param {string} html caller-built markup
 * @param {{label?: string}} [options]
 */
export function aboutMarkup(routeKey, html, { label = "About this page" } = {}) {
  return disclosureMarkup({ label, body: html, key: `about:${routeKey}`, open: false, className: "about-page" });
}

const remembering = new WeakSet();
// How long after the instructor's click on a summary its toggle still counts as theirs (the toggle event is a task queued by
// the click, normally a few milliseconds later; a busy page can delay it).
const TOGGLE_GRACE_MS = 3000;
/**
 * Remember the open state of every keyed disclosure under root, bound once per root. Only the instructor's own toggles are
 * remembered: a click on the disclosure's summary (a mouse click, or Enter or Space on the focused summary, which the browser
 * turns into one) marks it, and its toggle then stores the state. The browser also fires `toggle` when it parses
 * `<details open>` and when a script or a patch (`dom-patch.js`) changes `open`; those are not choices, and storing them made
 * a disclosure drawn open by default remembered as open on its first view, so its default never applied again (2026-10-02).
 * @param {Document | HTMLElement} [root]
 */
export function rememberDisclosures(root = globalThis.document) {
  if (!root || remembering.has(root)) return;
  remembering.add(root);
  /** @type {WeakMap<object, number>} */
  const clicked = new WeakMap();
  const now = () => globalThis.performance?.now?.() ?? Date.now();
  const keyedSummary = (/** @type {any} */ event) => {
    const summary = event.target?.closest?.("summary");
    const details = summary?.parentElement;
    return details?.tagName === "DETAILS" && details.dataset?.disclosureKey ? details : null;
  };
  root.addEventListener("click", (event) => {
    const details = keyedSummary(event);
    if (details) clicked.set(details, now());
  }, true);
  // A click a page handled itself (preventDefault) toggles nothing, so it leaves no mark for a later scripted toggle to use.
  root.addEventListener("click", (event) => {
    const details = event.defaultPrevented ? keyedSummary(event) : null;
    if (details) clicked.delete(details);
  });
  root.addEventListener("toggle", (event) => {
    const node = /** @type {HTMLDetailsElement} */ (event.target);
    const key = node?.dataset?.disclosureKey;
    if (!key) return;
    const at = clicked.get(node);
    if (at === undefined || now() - at > TOGGLE_GRACE_MS) return;
    clicked.delete(node);
    try { globalThis.localStorage?.setItem(DISCLOSURE_PREFIX + key, node.open ? "1" : "0"); } catch { /* a convenience only */ }
  }, true);
}

/**
 * An empty state: icon, a one-line title, one sentence, one action. kind "filtered" means records exist and a filter
 * hides them (offer to clear it); "first-use" means nothing exists yet.
 * @param {{icon?: string, title: string, body?: string, action?: {label: string, href?: string, data?: Record<string, unknown>, primary?: boolean} | null, kind?: "first-use" | "filtered", heading?: "h2" | "h3" | "h4"}} spec
 */
export function emptyMarkup({ icon = "", title, body = "", action = null, kind = "first-use", heading = "h3" }) {
  const tag = ["h2", "h3", "h4"].includes(heading) ? heading : "h3";
  return `<div class="empty-state" data-kind="${kind === "filtered" ? "filtered" : "first-use"}">${icon ? `<span class="empty-state-icon" data-icon="${escape(icon)}" aria-hidden="true">${iconSvg(icon)}</span>` : ""}<${tag}>${escape(title)}</${tag}>${body ? `<p>${escape(body)}</p>` : ""}${action ? actionMarkup(action) : ""}</div>`;
}

/**
 * The next step: what to do and a link to the page where it is done. It only links; it never performs the action.
 * @param {{eyebrow?: string, title: string, detail?: string, label: string, href: string}} spec
 */
export function nextStepMarkup({ eyebrow = "Next step", title, detail = "", label, href }) {
  const text = /→\s*$/.test(label) ? label : `${label} →`;
  return `<aside class="next-step" aria-label="Next step"><p class="eyebrow">${escape(eyebrow)}</p><strong class="next-step-title">${escape(title)}</strong>${detail ? `<p class="next-step-detail">${escape(detail)}</p>` : ""}<a class="button primary" href="${escape(href)}">${escape(text)}</a></aside>`;
}

/**
 * Filter chips: one pressed at a time. bindFilterChips(root, name, onChange) makes them work.
 * @param {string} name the filter's name (data-filter-group)
 * @param {{value: string, label: string, count?: number | null}[]} options
 * @param {string} selected
 * @param {{label?: string}} [options2]
 */
export function filterChipsMarkup(name, options, selected, { label = "Filter" } = {}) {
  return `<div class="filter-chips" role="group" aria-label="${escape(label)}" data-filter-group="${escape(name)}">${options.map((option) => `<button type="button" aria-pressed="${option.value === selected}" data-filter="${escape(option.value)}">${escape(option.label)}${option.count === null || option.count === undefined ? "" : ` <b>${escape(Number(option.count).toLocaleString())}</b>`}</button>`).join("")}</div>`;
}

/**
 * @param {HTMLElement | Document} root
 * @param {string} name
 * @param {(value: string) => void} onChange
 * @returns {() => void} unbind
 */
export function bindFilterChips(root, name, onChange) {
  const listener = (/** @type {Event} */ event) => {
    const target = /** @type {HTMLElement} */ (event.target);
    const button = /** @type {HTMLElement | null} */ (target?.closest?.("[data-filter]"));
    const group = /** @type {HTMLElement | null} */ (button?.closest?.("[data-filter-group]"));
    if (!button || !group || group.dataset.filterGroup !== name) return;
    if (button.getAttribute("aria-pressed") === "true") return;
    group.querySelectorAll("[data-filter]").forEach((chip) => chip.setAttribute("aria-pressed", String(chip === button)));
    onChange(button.dataset.filter || "");
  };
  root.addEventListener("click", listener);
  return () => root.removeEventListener("click", listener);
}

let menuCount = 0;
/**
 * A '⋯' action menu on a native popover. Items open the same dialogs as before (data-* attributes for the page's
 * handlers, or links). Pass a stable `id` when the view re-renders by patching. bindActionMenu(root) places and closes it.
 * @param {{id?: string, label?: string, items: {label: string, href?: string, data?: Record<string, unknown>, danger?: boolean, disabled?: boolean, title?: string}[]}} spec
 */
export function actionMenuMarkup({ id = "", label = "More actions", items }) {
  const menuId = id || `action-menu-${++menuCount}`;
  const item = (/** @type {typeof items[number]} */ entry) => {
    const attributes = `role="menuitem"${entry.danger ? ' class="danger"' : ""}${entry.title ? ` title="${escape(entry.title)}"` : ""}`;
    return entry.href
      ? `<a ${attributes} href="${escape(entry.href)}"${dataAttributes(entry.data)}>${escape(entry.label)}</a>`
      : `<button type="button" ${attributes}${dataAttributes(entry.data)}${entry.disabled ? " disabled" : ""}>${escape(entry.label)}</button>`;
  };
  return `<div class="action-menu"><button type="button" class="icon-button action-menu-trigger" popovertarget="${escape(menuId)}" aria-label="${escape(label)}" aria-haspopup="menu" aria-expanded="false" title="${escape(label)}">⋯</button><div id="${escape(menuId)}" popover class="action-menu-list" role="menu" aria-label="${escape(label)}">${items.map(item).join("")}</div></div>`;
}

const boundMenus = new WeakSet();
/**
 * Places each action menu under its button (CSSOM only), keeps aria-expanded true to its state, moves focus with the
 * arrow keys and closes the menu after a choice. Idempotent per root.
 * @param {HTMLElement | Document} root
 */
export function bindActionMenu(root) {
  if (!root || boundMenus.has(root)) return;
  boundMenus.add(root);
  root.addEventListener("toggle", (event) => {
    const menu = /** @type {HTMLElement} */ (event.target);
    if (!menu?.classList?.contains("action-menu-list")) return;
    const open = /** @type {any} */ (event).newState === "open";
    const trigger = menu.closest(".action-menu")?.querySelector(".action-menu-trigger");
    trigger?.setAttribute("aria-expanded", String(open));
    if (!open || !trigger) return;
    const box = trigger.getBoundingClientRect();
    const width = menu.offsetWidth, height = menu.offsetHeight;
    const left = Math.max(8, Math.min(box.right - width, innerWidth - width - 8));
    const below = box.bottom + 4, above = box.top - height - 4;
    menu.style.left = `${left}px`;
    menu.style.top = `${below + height > innerHeight - 8 && above > 8 ? above : below}px`;
    /** @type {HTMLElement | null} */ (menu.querySelector("[role=menuitem]:not(:disabled)"))?.focus();
  }, true);
  root.addEventListener("keydown", (/** @type {Event} */ keyEvent) => {
    const event = /** @type {KeyboardEvent} */ (keyEvent);
    const target = /** @type {HTMLElement} */ (event.target);
    const menu = target?.closest?.(".action-menu-list");
    if (!menu || !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const items = [...menu.querySelectorAll("[role=menuitem]:not(:disabled)")];
    const index = items.indexOf(target);
    const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
      : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    event.preventDefault();
    /** @type {HTMLElement} */ (items[next])?.focus();
  });
  root.addEventListener("click", (event) => {
    const target = /** @type {HTMLElement} */ (event.target);
    const item = target?.closest?.(".action-menu-list [role=menuitem]");
    // Close after the page's own handler has seen the click (it bubbles on to document).
    if (item) queueMicrotask(() => /** @type {any} */ (item.closest(".action-menu-list"))?.hidePopover?.());
  });
}

/**
 * Grey bars where content is coming; the label is for screen readers. Only for a view's first mount.
 * @param {string} [label]
 * @param {number} [rows]
 */
export function loadingMarkup(label = "Loading…", rows = 3) {
  const count = Math.max(1, Math.min(12, Math.round(rows) || 3));
  return `<div class="loading-state loading-skeleton" role="status" aria-busy="true"><span class="sr-only">${escape(label)}</span>${"<i></i>".repeat(count)}</div>`;
}

/**
 * How a script scrolls: smoothly, unless the visitor prefers reduced motion (DESIGN-LANGUAGE).
 * @returns {ScrollBehavior}
 */
export function scrollBehavior() {
  return matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}

/**
 * Runs a page's timed read now when the tab is shown, else once, when the tab is shown again: a hidden tab reads nothing.
 * Only for polls that read and redraw, never for the waits of an action; `run` still checks that its page is on screen.
 * @param {() => unknown} run
 * @param {Document} [doc]
 */
export function whenVisible(run, doc = globalThis.document) {
  if (!doc?.hidden) { run(); return; }
  const go = () => {
    if (doc.hidden) return;
    doc.removeEventListener("visibilitychange", go);
    run();
  };
  doc.addEventListener("visibilitychange", go);
}

/* Leave guard: views register a function that returns a message while they hold unsaved work (null otherwise). The shell
   asks before a hash change or an unload; declining keeps the page and its address. */
const leaveGuards = new Set();
/** @type {Map<object, HTMLElement | null>} */
const captureFocusHolds = new Map();
const CAPTURE_SURFACES = ".assistant-area-selector, .assistant-area-bubble, #assistant-panel";
/**
 * Protect a visible editor while capture/chat temporarily borrows focus (GRADEBOOK, ASSISTANT "Selected-area conversations").
 * The protection lasts only while focus stays in the editor or an assistant surface: once the instructor clicks or tabs into
 * the page elsewhere, the hold ends without refocusing and the editor gets its deferred blur, so its owner's own rule runs
 * (a gradebook cell commits). The returned release refocuses the editor; `{focus: false}` delivers the blur instead.
 * @param {HTMLElement | null} element @param {string} [surfaces] selectors of the assistant surfaces that keep the hold
 * @returns {(options?: {focus?: boolean}) => void}
 */
export function holdCaptureFocus(element, surfaces = CAPTURE_SURFACES) {
  const hold = {}, doc = globalThis.document;
  captureFocusHolds.set(hold, element);doc.documentElement.dataset.assistantCaptureActive = 'true';
  const blur = (/** @type {FocusEvent} */ event) => { if (event.target === element) event.stopImmediatePropagation(); };
  const end = () => {
    doc.removeEventListener('blur', blur, true);doc.removeEventListener('focusin', leave, true);doc.removeEventListener('pointerdown', leave, true);
    captureFocusHolds.delete(hold);
    if (!captureFocusHolds.size) delete doc.documentElement.dataset.assistantCaptureActive;
  };
  const deliverBlur = () => { if (element?.isConnected && doc.activeElement !== element) element.dispatchEvent(new (globalThis.FocusEvent || Event)('blur')); };
  function leave(/** @type {Event} */ event) {
    const node = /** @type {any} */ (event.target), target = node?.nodeType === 1 ? node : node?.parentElement;
    if (!captureFocusHolds.has(hold) || !target || target === element || element?.contains?.(target) || target.closest?.(surfaces)) return;
    end();deliverBlur();
  }
  doc.addEventListener('blur', blur, true);doc.addEventListener('focusin', leave, true);doc.addEventListener('pointerdown', leave, true);
  return ({focus = true} = {}) => {
    if (!captureFocusHolds.has(hold)) return;
    if (focus && element?.isConnected) element.focus({preventScroll: true});
    end();
    if (!focus) deliverBlur();
  };
}
/** True only for the element an active capture/chat hold protects: its owner defers that element's blur. @param {Element | null | undefined} element */
export function isCaptureHeld(element) {
  return Boolean(element) && [...captureFocusHolds.values()].includes(/** @type {HTMLElement} */ (element));
}
/** @param {() => (string | null | undefined | false)} guard @returns {() => void} unregister */
export function registerLeaveGuard(guard) {
  leaveGuards.add(guard);
  return () => { leaveGuards.delete(guard); };
}
/** The first message of a guard holding unsaved work, or null. */
export function leaveMessage() {
  for (const guard of leaveGuards) {
    try {
      const message = guard();
      if (message) return String(message);
    } catch { /* a failing guard never traps the instructor on a page */ }
  }
  return null;
}

let leaveGuardInstalled = false;
/**
 * The shell calls this once, before any view listens to hashchange: a declined navigation stops the event there and
 * puts the previous address back without rendering anything.
 * @param {{window?: any, confirm?: (message: string) => boolean}} [environment]
 */
export function installLeaveGuard({ window: win = globalThis.window, confirm = (message) => win.confirm(message) } = {}) {
  if (leaveGuardInstalled || !win) return;
  leaveGuardInstalled = true;
  guardCountsHashChanges = true;
  let current = win.location.href;
  win.addEventListener("hashchange", (/** @type {HashChangeEvent} */ event) => {
    const message = leaveMessage();
    if (message && !confirm(message)) {
      event.stopImmediatePropagation();
      win.history.replaceState(win.history.state, "", event.oldURL || current);
      return;
    }
    current = win.location.href;
    // An accepted navigation abandons the dialogs being prepared (captureDialogIntent). It is counted here, ahead of every view's
    // own hashchange listener: a view that captures an intent while it renders this very navigation (a Settings hub link that
    // opens a dialog on the page it lands on) would otherwise find its intent abandoned by the navigation that opened it.
    intentNavigation++;
  });
  win.addEventListener("beforeunload", (/** @type {BeforeUnloadEvent} */ event) => {
    if (!leaveMessage()) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

/* Campus dates are wall-clock dates: an ISO date is shown as that date, never converted through a time zone. */
const DATE_FORMAT = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
const DATE_YEAR_FORMAT = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
/** @param {string} iso */
function isoDay(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso ?? ""));
  return match ? Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])) : null;
}
/**
 * 'Thu, Sep 17' for 2026-09-17 (or its date part of a longer ISO string), with the year when asked. Unreadable input
 * comes back unchanged.
 * @param {string} iso
 * @param {{year?: boolean}} [options]
 */
export function wallDate(iso, { year = false } = {}) {
  const day = isoDay(iso);
  return day === null ? String(iso ?? "") : (year ? DATE_YEAR_FORMAT : DATE_FORMAT).format(day);
}

/** Today's campus date (YYYY-MM-DD, America/Chicago), whatever the browser's time zone; the date of an instant, if one is given. */
export function localToday(now = new Date()) {
  return campusToday(now);
}

/**
 * 'today', 'tomorrow', 'in 26 days', 'yesterday', '9 days ago' from one campus date to another.
 * @param {string} iso
 * @param {string} [campusToday] YYYY-MM-DD; the campus date by default
 */
export function daysFrom(iso, campusToday = localToday()) {
  const day = isoDay(iso), today = isoDay(campusToday);
  if (day === null || today === null) return "";
  const days = Math.round((day - today) / 86400000);
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  return days > 0 ? `in ${days.toLocaleString()} days` : `${(-days).toLocaleString()} days ago`;
}

/* Human names for the derived states the server reports. Raw codes are never shown to the instructor. */
/** @type {Record<string, string>} */
const STATE_LABELS = {
  todo: "Needs you", check: "Needs you", wait: "Working", done: "Nothing to do", info: "For your information",
  needs_review: "Needs review", pending: "Queued", queued: "Queued", running: "Running", failed: "Failed",
  cancelled: "Cancelled", canceled: "Cancelled", complete: "Completed", completed: "Completed", deleted: "Deleted",
  ready: "Ready to print", rendering: "Preparing", validating: "Checking", superseded: "Superseded",
  idle: "Not started", started: "In progress",
};
/**
 * @param {string} code a state such as "todo", "review_ready" or "READY"
 * @param {{waiting?: number}} [context] review_ready is 'Ready for review' while something waits, else 'Finished'
 */
export function stateLabel(code, { waiting = 0 } = {}) {
  const key = String(code ?? "").trim().toLowerCase();
  if (!key) return "";
  if (key === "review_ready") return waiting > 0 ? "Ready for review" : "Finished";
  if (Object.hasOwn(STATE_LABELS, key)) return STATE_LABELS[key];
  const words = key.replace(/[_-]+/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/* Keyboard shortcuts: ONE registry with shared key listeners. A shortcut never fires while the instructor types (inputs,
   text areas, selects, editable text), inside a grid that uses its own keys, or while a dialog other than the command
   palette is open, and never for an event something else already handled. '?' belongs to the shell's shortcut sheet,
   which lists listShortcuts(). The most recent active registration of a key wins. */
/** @type {{key: string, label: string, run: (event: KeyboardEvent) => void, release?: (event: KeyboardEvent) => void, cancel?: () => void, when?: () => boolean, allowInInput: boolean, allowInGrid: boolean, allowInDialog: boolean, scope: string, group?: {key: string, label: string}, combo: ReturnType<typeof parseShortcut>}[]} */
const shortcuts = [];
let shortcutsBound = false;
let metaTap = {pressed:false, downAt:0, releasedAt:/** @type {number | null} */ (null)};
/** @type {(typeof shortcuts)[number] | null} */
let metaHold = null;
let metaHoldTimer = 0;
const META_TAP_MS = 600, META_DOUBLE_MS = 700, META_STILL_PX = 4;
/** @type {{x: number, y: number} | null} */
let pointerAt = null;
/** @type {{x: number, y: number} | null} */
let holdFrom = null;

/**
 * FRONTEND: a selection hold arms only while the pointer is still. Moving more than META_STILL_PX before the 600 ms elapse
 * (reaching for a link to Command+click) drops the pending hold; quick taps and an armed hold are unaffected.
 * @param {{clientX: number, clientY: number}} event
 */
export function noteShortcutPointer(event) {
  pointerAt = {x: event.clientX, y: event.clientY};
  if (!metaHoldTimer || metaHold) return;
  if (!holdFrom) { holdFrom = pointerAt; return; }
  if (Math.hypot(pointerAt.x - holdFrom.x, pointerAt.y - holdFrom.y) > META_STILL_PX) { clearTimeout(metaHoldTimer); metaHoldTimer = 0; }
}

/** Clear partial modifier gestures after another input, leaving the tab, or losing focus. */
export function resetShortcutTaps() {
  clearTimeout(metaHoldTimer);metaHoldTimer=0;holdFrom=null;
  const held=metaHold;metaHold=null;
  metaTap = {pressed:false, downAt:0, releasedAt:null};
  held?.cancel?.();
}

/** @param {string} descriptor "?", "/", "Mod+K", "Alt+A", "Shift+N" */
function parseShortcut(descriptor) {
  const parts = descriptor.split("+");
  const key = parts.length > 1 && parts.at(-1) === "" ? "+" : parts.at(-1) || "";
  const mods = new Set(parts.slice(0, parts.length > 1 && parts.at(-1) === "" ? -2 : -1).map((part) => part.toLowerCase()));
  return { key, doubleMeta: descriptor === 'Meta Meta', holdMeta: descriptor === 'Hold Meta', mod: mods.has("mod"), ctrl: mods.has("ctrl"), meta: mods.has("meta"), alt: mods.has("alt"), shift: mods.has("shift") };
}

/** @param {KeyboardEvent} event @param {ReturnType<typeof parseShortcut>} combo */
function matches(event, combo) {
  if (combo.doubleMeta || combo.holdMeta) return false;  // Modifier gestures have their own lifecycle below.
  const letter = /^[a-z]$/i.test(combo.key);
  // With Alt, macOS types a different character (Option+A is "å", Option+N a dead key), so a letter also matches by its
  // physical key, `event.code` "KeyA".
  const keyMatches = letter
    ? event.key?.toLowerCase() === combo.key.toLowerCase() || (combo.alt && event.code === `Key${combo.key.toUpperCase()}`)
    : event.key === combo.key;
  if (!keyMatches) return false;
  const primary = event.ctrlKey || event.metaKey;
  if (combo.mod) { if (!primary) return false; }
  else if (Boolean(event.ctrlKey) !== combo.ctrl || Boolean(event.metaKey) !== combo.meta) return false;
  if (Boolean(event.altKey) !== combo.alt) return false;
  if (letter && Boolean(event.shiftKey) !== combo.shift) return false;
  return true;
}

/** @param {KeyboardEvent} event @param {Document} [doc] @param {{allowInGrid?: boolean, allowInDialog?: boolean}} [entry] */
export function shortcutBlocked(event, doc = globalThis.document, entry = {}) {
  if (event.defaultPrevented) return true;
  const target = /** @type {HTMLElement} */ (event.target);
  if (target?.closest?.('[role="grid"]') && !entry.allowInGrid) return true;
  if (doc?.querySelector?.("dialog[open]:not(.palette)") && !entry.allowInDialog) return true;
  return false;
}

/** @param {KeyboardEvent} event */
function typing(event) {
  const target = /** @type {HTMLElement} */ (event.target);
  return Boolean(target && (/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName || "") || target.isContentEditable));
}

/**
 * FRONTEND: two quick Command taps open chat; a sustained bare Command owns selection until its release.
 * Observed during capture so a grid handling Cmd+C still cancels a partial tap, without preventing that chord.
 * @param {KeyboardEvent} event @param {Document} [doc] @param {number} [now]
 */
export function dispatchShortcutTap(event, doc = globalThis.document, now = performance.now()) {
  const command = event.key === 'Meta' || event.code === 'MetaLeft' || event.code === 'MetaRight';
  if (!command || event.isComposing || event.defaultPrevented || event.altKey || event.ctrlKey || event.shiftKey) {
    const escape=Boolean(metaHold)&&event.key==='Escape';
    resetShortcutTaps();
    if(escape){event.preventDefault();event.stopImmediatePropagation();}
    return escape;
  }
  if (event.type === 'keydown') {
    if (event.repeat && metaHold) return false;
    if (event.repeat || metaTap.pressed) {resetShortcutTaps();return false;}
    metaTap.pressed=true;metaTap.downAt=now;
    const entry=[...shortcuts].reverse().find(entry=>entry.combo.holdMeta && !shortcutBlocked(event,doc,entry)
      && (!typing(event)||entry.allowInInput) && shortcutEnabled(entry));
    holdFrom=pointerAt;
    if (entry) metaHoldTimer=setTimeout(()=>{
      metaHoldTimer=0;
      if(!metaTap.pressed || !shortcuts.includes(entry) || !shortcutEnabled(entry))return;
      metaTap.releasedAt=null;metaHold=entry;entry.run(event);
    },META_TAP_MS);
    return false;
  }
  if (event.type==='keyup' && metaHold && !event.metaKey) {
    const held=metaHold;metaHold=null;resetShortcutTaps();event.preventDefault();held.release?.(event);return true;
  }
  if (event.type !== 'keyup' || !metaTap.pressed || event.metaKey || now-metaTap.downAt>META_TAP_MS) {
    resetShortcutTaps();return false;
  }
  const previous=metaTap.releasedAt;
  clearTimeout(metaHoldTimer);metaHoldTimer=0;
  metaTap.pressed=false;metaTap.releasedAt=now;
  if (previous===null || now-previous>META_DOUBLE_MS) return false;
  resetShortcutTaps();
  for(let index=shortcuts.length-1;index>=0;index--) {
    const entry=shortcuts[index];
    if (!entry.combo.doubleMeta || shortcutBlocked(event,doc,entry) || (typing(event)&&!entry.allowInInput) || !shortcutEnabled(entry)) continue;
    event.preventDefault();entry.run(event);return true;
  }
  return false;
}

/** @param {(typeof shortcuts)[number]} entry */
function shortcutEnabled(entry) {
  try {return entry.when?Boolean(entry.when()):true;} catch {return false;}
}

/** @param {KeyboardEvent} event */
export function dispatchShortcut(event, doc = globalThis.document) {
  if (event.defaultPrevented) return false;
  for (let index = shortcuts.length - 1; index >= 0; index -= 1) {
    const entry = shortcuts[index];
    if (shortcutBlocked(event, doc, entry)) continue;
    if (!matches(event, entry.combo) || (typing(event) && !entry.allowInInput) || !shortcutEnabled(entry)) continue;
    event.preventDefault();
    entry.run(event);
    return true;
  }
  return false;
}

/**
 * `group` lists several keys as ONE row of the '?' sheet (e.g. the digits 1–9: `{key: "1–9", label: "Tick that rubric line"}`).
 * @param {{key: string, label: string, run: (event: KeyboardEvent) => void, release?: (event: KeyboardEvent) => void, cancel?: () => void, when?: () => boolean, allowInInput?: boolean, allowInGrid?: boolean, allowInDialog?: boolean, scope?: "global" | "page", group?: {key: string, label: string}}} spec
 * @returns {() => void} unregister (views call it when they leave)
 */
export function registerShortcut({ key, label, run, release, cancel, when, allowInInput = false, allowInGrid = false, allowInDialog = false, scope = "page", group }) {
  const entry = { key, label, run, release, cancel, when, allowInInput, allowInGrid, allowInDialog, scope, group, combo: parseShortcut(key) };
  shortcuts.push(entry);
  if (!shortcutsBound && globalThis.document) {
    shortcutsBound = true;
    globalThis.document.addEventListener("keydown", (event) => { dispatchShortcut(event); });
    globalThis.document.addEventListener('keydown',event=>{dispatchShortcutTap(event);},true);
    globalThis.document.addEventListener('keyup',event=>{dispatchShortcutTap(event);},true);
    globalThis.document.addEventListener('pointerdown',()=>{if(!metaHold)resetShortcutTaps();},true);
    globalThis.document.addEventListener('pointermove',noteShortcutPointer,{capture:true,passive:true});
    globalThis.document.addEventListener('visibilitychange',resetShortcutTaps);
    globalThis.window?.addEventListener('blur',resetShortcutTaps);
  }
  return () => {
    if(metaHold===entry)resetShortcutTaps();
    const index = shortcuts.indexOf(entry);
    if (index >= 0) shortcuts.splice(index, 1);
  };
}

/** The shortcuts active right now, newest registration of each key only (for the '?' sheet). */
export function listShortcuts() {
  const seen = new Set();
  /** @type {{key: string, label: string, scope: string, group?: string}[]} */
  const active = [];
  for (let index = shortcuts.length - 1; index >= 0; index -= 1) {
    const entry = shortcuts[index];
    if (!shortcutEnabled(entry) || seen.has(entry.key)) continue;
    seen.add(entry.key);
    const group = entry.group;
    if (group) {
      // One row per group, placed where its oldest active member would be.
      const at = active.findIndex((item) => item.group === group.key);
      if (at >= 0) active.splice(at, 1);
      active.unshift({ key: group.key, label: group.label, scope: entry.scope, group: group.key });
      continue;
    }
    active.unshift({ key: entry.combo.doubleMeta ? '⌘ ⌘' : entry.combo.holdMeta ? 'Hold ⌘' : entry.key, label: entry.label, scope: entry.scope });
  }
  return active;
}

/* Lazily loaded modules. Views warm them at creation (FRONTEND.md); if one still cannot be fetched (a tab opened before
   Faculty was updated and restarted), the page says so once and offers a reload that keeps the address. */
let updatedShown = false;
/** Show the persistent 'Faculty was updated' notice at the top of the page (once). */
export function showUpdatedNotice(doc = globalThis.document) {
  if (updatedShown || !doc) return;
  updatedShown = true;
  const host = doc.querySelector("main") || doc.body;
  if (!host) return;
  const wrapper = doc.createElement("div");
  wrapper.innerHTML = calloutMarkup({ tone: "warning", title: "Faculty was updated since this tab opened", body: "Reload to use the new version; this page’s address is kept.", action: { label: "Reload", data: { appReload: "" } }, id: "app-updated", className: "app-updated" });
  const notice = wrapper.firstElementChild;
  if (!notice) return;
  notice.querySelector("[data-app-reload]")?.addEventListener("click", () => { globalThis.location?.reload(); });
  host.prepend(notice);
}

/**
 * Dynamic import with the 'Faculty was updated · Reload' net. `specifier` is a path relative to this directory
 * ("./gradebook-setup.js") or a loader function (() => import("./x.js")) so the caller's own relative path is kept.
 * The failure is rethrown so the caller can stop what it was doing.
 * @param {string | (() => Promise<any>)} specifier
 */
export async function loadModule(specifier) {
  try {
    return typeof specifier === "function" ? await specifier() : await import(new URL(specifier, import.meta.url).href);
  } catch (cause) {
    showUpdatedNotice();
    throw cause;
  }
}
