import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// ui.js imports icons.js, so it is loaded from its file (a data: URL cannot resolve relative imports).
const ui = await import(new URL("../static/ui.js", import.meta.url).href);
const { runFormAction, dialogSession } = ui;
const source = await readFile(new URL("../static/ui.js", import.meta.url), "utf8");

class Element {
  constructor() { this.hidden = true; this.textContent = ""; }
}
globalThis.HTMLElement = Element;

function formFixture() {
  const buttons = [{ disabled: false }, { disabled: true }];
  const error = new Element();
  const warning = new Element(); warning.hidden = false; warning.textContent = "Issued paper exists.";
  const attributes = new Map();
  const form = {
    dataset: {},
    querySelectorAll: () => buttons,
    querySelector: (selector) => selector.includes(".form-error") ? error : warning,
    getAttribute: (name) => attributes.get(name) ?? null,
    setAttribute: (name, value) => attributes.set(name, value),
    removeAttribute: (name) => attributes.delete(name),
  };
  return { form, buttons, error, warning };
}

test("one form action runs at a time and existing disabled controls remain disabled", async () => {
  const { form, buttons } = formFixture();
  let complete, calls = 0;
  const pending = runFormAction(form, () => {
    calls += 1; return new Promise((resolve) => { complete = resolve; });
  });
  assert.ok(buttons.every((button) => button.disabled));
  assert.equal(form.getAttribute("aria-busy"), "true");
  await runFormAction(form, async () => { calls += 1; });
  complete(); await pending;
  assert.equal(calls, 1);
  assert.deepEqual(buttons.map((button) => button.disabled), [false, true]);
  assert.equal(form.getAttribute("aria-busy"), null);
});

test("keepClose leaves only the dialog's header Close enabled while a read runs, and restores the rest", async () => {
  const headerClose = { disabled: false, dataset: { action: "close" }, closest: (selector) => selector === ".dialog-header" ? {} : null };
  const footerCancel = { disabled: false, dataset: { action: "close" }, closest: () => null };
  const review = { disabled: false, dataset: {}, closest: () => null };
  const { form } = formFixture();
  form.querySelectorAll = () => [headerClose, footerCancel, review];
  let complete;
  const pending = runFormAction(form, () => new Promise((resolve) => { complete = resolve; }), { keepClose: true });
  assert.deepEqual([headerClose.disabled, footerCancel.disabled, review.disabled], [false, true, true], "only the header Close is left usable");
  assert.equal(form.dataset.closable, "true", "Escape may close it too (app.js)");
  complete(); await pending;
  assert.deepEqual([headerClose.disabled, footerCancel.disabled, review.disabled], [false, false, false]);
  assert.equal(form.dataset.closable, undefined);
  // Without the option every button is disabled, the header Close included (a write keeps its dialog open).
  const second = runFormAction(form, () => new Promise((resolve) => { complete = resolve; }));
  assert.deepEqual([headerClose.disabled, footerCancel.disabled, review.disabled], [true, true, true]);
  assert.equal(form.dataset.closable, undefined, "a write holds Escape back");
  complete(); await second;
  assert.deepEqual([headerClose.disabled, footerCancel.disabled, review.disabled], [false, false, false]);
});

test("a control is busy only while its own request runs; a blocked one is never marked busy", () => {
  const { setBusy, markBusy } = ui;
  const attributes = new Map();
  const button = { disabled: false, setAttribute: (name, value) => attributes.set(name, value), removeAttribute: (name) => attributes.delete(name) };
  setBusy(button, true);
  assert.equal(button.disabled, true);
  assert.equal(attributes.get("aria-busy"), "true");
  setBusy(button, false);
  assert.equal(button.disabled, false);
  assert.equal(attributes.has("aria-busy"), false);
  button.disabled = true; markBusy(button, false);
  assert.equal(button.disabled, true, "markBusy leaves the derived disabled state alone");
  assert.equal(attributes.has("aria-busy"), false);
  setBusy(null, true);  // a control that is not on the page is skipped
  // The busy cursor is keyed to these markers, everything else disabled shows not-allowed.
  return readFile(new URL("../static/style.css", import.meta.url), "utf8").then((css) => {
    assert.match(css, /button:disabled \{\s*cursor: not-allowed;/);
    assert.match(css, /button\[aria-busy="true"\],\s*:is\(\[aria-busy="true"\], \[data-saving="true"\], \[data-busy="true"\]\) button:disabled \{\s*cursor: wait;/);
  });
});

test("failed forms show the error while retaining destructive-action warnings", async () => {
  const { form, error, warning } = formFixture();
  await runFormAction(form, async () => { throw new Error("The chart changed."); });
  assert.equal(error.textContent, "The chart changed.");
  assert.equal(error.hidden, false);
  assert.equal(warning.hidden, false);
  assert.equal(warning.textContent, "Issued paper exists.");
  assert.equal(form.dataset.saving, "false");
});

test("late responses do not populate closed, detached, or replaced dialog content", () => {
  const host = { isConnected: true };
  let content = host;
  const dialog = { open: true, contains: (node) => node === content };
  const active = dialogSession(host, dialog);
  assert.equal(active(), true);
  dialog.open = false; assert.equal(active(), false);
  dialog.open = true; content = {}; assert.equal(active(), false);
  content = host; host.isConnected = false; assert.equal(active(), false);
});

test("a programmatic scroll is instant under reduced motion and smooth otherwise, read when it happens", () => {
  const saved = Object.getOwnPropertyDescriptor(globalThis, "matchMedia");
  let reduced = true;
  const queries = [];
  Object.defineProperty(globalThis, "matchMedia", { configurable: true, writable: true, value: (query) => { queries.push(query); return { matches: reduced }; } });
  try {
    assert.equal(ui.scrollBehavior(), "auto");
    reduced = false;
    assert.equal(ui.scrollBehavior(), "smooth", "the preference is read at each scroll, not once at load");
    assert.deepEqual(queries, ["(prefers-reduced-motion: reduce)", "(prefers-reduced-motion: reduce)"]);
  } finally {
    if (saved) Object.defineProperty(globalThis, "matchMedia", saved); else delete globalThis.matchMedia;
  }
});

test("dialog option reads keep only the latest opening and its original UI context", () => {
  const priorLocation = globalThis.location;
  globalThis.location = { href: "https://faculty.test/#seating" };
  try {
    const content = {};
    const dialog = { open: false, firstElementChild: content };
    const first = ui.captureDialogIntent(dialog), second = ui.captureDialogIntent(dialog);
    assert.equal(first(), false, "the newer opening supersedes the older read");
    assert.equal(second(), true);
    dialog.firstElementChild = {};
    assert.equal(second(), false, "a replacement dialog stays in place");
    dialog.firstElementChild = content;
    dialog.open = true;
    assert.equal(second(), false, "opening the current content invalidates a background read");
    const third = ui.captureDialogIntent(dialog);
    globalThis.location.href = "https://faculty.test/#courses";
    assert.equal(third(), false, "navigation cancels a pending opening");
  } finally {
    if (priorLocation === undefined) delete globalThis.location;
    else globalThis.location = priorLocation;
  }
});

/* ---- Component vocabulary helpers (UX overhaul, wave 1) ---- */

test("escape covers text and quoted attributes", () => {
  assert.equal(ui.escape(`<a href="x" title='y'>&</a>`), "&lt;a href=&quot;x&quot; title=&#39;y&#39;&gt;&amp;&lt;/a&gt;");
  assert.equal(ui.escape(null), "");
  assert.equal(ui.escape(3), "3");
});

test("fold lower-cases and strips accents, for matching typed text against names", () => {
  assert.equal(ui.fold("José Peña"), "jose pena");
  assert.equal(ui.fold("Zoe\u0308 Tran, 507"), "zoe tran, 507");
  assert.equal(ui.fold(null), "");
  assert.equal(ui.fold(undefined), "");
  assert.equal(ui.fold(42), "42");
});

test("plural names a count and its noun, singular only for exactly one", () => {
  assert.equal(ui.plural(0, "copy", "copies"), "0 copies");
  assert.equal(ui.plural(1, "copy", "copies"), "1 copy");
  assert.equal(ui.plural(2, "copy", "copies"), "2 copies");
  assert.equal(ui.plural(2, "student"), "2 students");
  assert.equal(ui.plural(1, "student"), "1 student");
  assert.equal(ui.plural(-1, "page"), "-1 pages");
  assert.equal(ui.plural(1.5, "page"), "1.5 pages");
  assert.equal(ui.plural("1", "page"), "1 pages");
  // Plain digits: no thousands grouping.
  assert.equal(ui.plural(1000, "copy", "copies"), "1000 copies");
});

test("callouts escape their text, carry one tone and use role=alert only for danger", () => {
  const warning = ui.calloutMarkup({ tone: "warning", title: "3 <papers>", body: "Decide them.", action: { label: "Decide", href: "#grading/p?tab=rubrics&focus=versions" } });
  assert.match(warning, /^<div class="callout" data-tone="warning" role="status">/);
  assert.match(warning, /<strong class="callout-title">3 &lt;papers&gt;<\/strong><p>Decide them\.<\/p>/);
  assert.match(warning, /<a class="button callout-action" href="#grading\/p\?tab=rubrics&amp;focus=versions">Decide<\/a>/);
  const danger = ui.calloutMarkup({ tone: "danger", title: "Failed", action: { label: "Try again", data: { retryJob: "j1" } } });
  assert.match(danger, /role="alert"/);
  assert.match(danger, /<button type="button" class="button callout-action" data-retry-job="j1">Try again<\/button>/);
  assert.match(ui.calloutMarkup({ tone: "loud", title: "x" }), /data-tone="info"/);
  assert.doesNotMatch(ui.calloutMarkup({ title: "x" }), /<p>/);
});

test("disclosures use the shared markup and remember their open state per viewer", () => {
  const store = new Map();
  globalThis.localStorage = { getItem: (key) => store.get(key) ?? null, setItem: (key, value) => store.set(key, value) };
  try {
    const closed = ui.disclosureMarkup({ label: "Files & history", body: "<p>x</p>", hint: 3, key: "inbox-record", className: "inbox-record" });
    assert.equal(closed, '<details class="disclosure inbox-record" data-disclosure-key="inbox-record"><summary><span>Files &amp; history</span><span class="disclosure-hint">3</span></summary><div class="disclosure-body"><p>x</p></div></details>');
    assert.match(ui.disclosureMarkup({ label: "A", body: "", open: true }), /<details class="disclosure" open>/);
    // The instructor's own toggle (a click on the summary, then the browser's toggle) is remembered and wins over the default.
    const listeners = { click: [], toggle: [] };
    const root = { addEventListener: (type, fn, capture = false) => { listeners[type].push({ fn, capture }); } };
    ui.rememberDisclosures(root);
    ui.rememberDisclosures(root);  // bound once
    assert.equal(listeners.toggle.length, 1);
    assert.equal(listeners.toggle[0].capture, true);
    assert.deepEqual(listeners.click.map((entry) => entry.capture), [true, false]);
    const details = (key, open) => ({ tagName: "DETAILS", open, dataset: { disclosureKey: key } });
    const summaryOf = (node) => ({ closest: (selector) => selector === "summary" ? { parentElement: node } : null });
    const click = (node, prevented = false) => {
      const event = { target: summaryOf(node), defaultPrevented: false };
      listeners.click[0].fn(event);
      event.defaultPrevented = prevented;
      listeners.click[1].fn(event);
    };
    const toggle = (node) => listeners.toggle[0].fn({ target: node });
    const about = details("about:inbox", true);
    click(about); toggle(about);
    assert.equal(store.get("faculty:disclosure:about:inbox"), "1");
    assert.match(ui.aboutMarkup("inbox", "<p>How the Inbox works.</p>"), /^<details class="disclosure about-page" data-disclosure-key="about:inbox" open><summary><span>About this page<\/span><\/summary>/);
    assert.doesNotMatch(ui.aboutMarkup("grading", "<p>x</p>"), / open>/);
    about.open = false; click(about); toggle(about);
    assert.doesNotMatch(ui.aboutMarkup("inbox", ""), / open>/);
    // The toggle the browser fires when it PARSES <details open> (or a script or patch sets `open`) is no choice: a disclosure
    // drawn open by default is not remembered as open, so its default still applies later (2026-10-02).
    const drawn = details("grading:fold", true);
    toggle(drawn);
    assert.equal(store.has("faculty:disclosure:grading:fold"), false);
    assert.match(ui.disclosureMarkup({ label: "F", body: "", key: "grading:fold", open: false }), /data-disclosure-key="grading:fold"><summary>/);
    // One click marks one toggle: the next scripted toggle of the same disclosure is not stored either.
    click(drawn); drawn.open = false; toggle(drawn);
    assert.equal(store.get("faculty:disclosure:grading:fold"), "0");
    drawn.open = true; toggle(drawn);
    assert.equal(store.get("faculty:disclosure:grading:fold"), "0");
    // A click the page handled itself (preventDefault) toggles nothing and leaves no mark behind.
    const handled = details("handled", false);
    click(handled, true); handled.open = true; toggle(handled);
    assert.equal(store.has("faculty:disclosure:handled"), false);
    // An unkeyed disclosure is never stored.
    const plain = { tagName: "DETAILS", open: true, dataset: {} };
    click(plain); toggle(plain);
    assert.equal([...store.keys()].some((key) => key.endsWith("undefined")), false);
    // Storage that throws never breaks the page.
    globalThis.localStorage = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
    assert.equal(ui.rememberedOpen("about:inbox"), null);
    const blocked = details("about:inbox", true);
    click(blocked); toggle(blocked);
    assert.match(ui.aboutMarkup("inbox", ""), /about-page" data-disclosure-key="about:inbox"><summary>/);
  } finally {
    delete globalThis.localStorage;
  }
});

test("empty states separate nothing yet from filtered to nothing", () => {
  const first = ui.emptyMarkup({ icon: "book", title: "No courses yet", body: "Add a course.", action: { label: "Add a course", data: { action: "add-course" }, primary: true } });
  assert.match(first, /^<div class="empty-state" data-kind="first-use"><span class="empty-state-icon" data-icon="book" aria-hidden="true"><svg class="icon /);
  assert.match(first, /<h3>No courses yet<\/h3><p>Add a course\.<\/p><button type="button" class="button primary" data-action="add-course">Add a course<\/button><\/div>$/);
  const filtered = ui.emptyMarkup({ title: "No bookings match these filters", kind: "filtered", action: { label: "Clear filters", data: { clearFilters: "" } }, heading: "h2" });
  assert.equal(filtered, '<div class="empty-state" data-kind="filtered"><h2>No bookings match these filters</h2><button type="button" class="button" data-clear-filters="">Clear filters</button></div>');
  assert.match(ui.emptyMarkup({ title: "x", heading: "script" }), /<h3>x<\/h3>/);
});

test("the next-step callout only links", () => {
  const markup = ui.nextStepMarkup({ title: "Find whose paper each is", detail: "They look like papers printed without labels.", label: "Students", href: "#inbox/job/j1/students" });
  assert.equal(markup, '<aside class="next-step" aria-label="Next step"><p class="eyebrow">Next step</p><strong class="next-step-title">Find whose paper each is</strong><p class="next-step-detail">They look like papers printed without labels.</p><a class="button primary" href="#inbox/job/j1/students">Students →</a></aside>');
  assert.match(ui.nextStepMarkup({ eyebrow: "Stage 3 of 6", title: "t", label: "Open →", href: "#x" }), /<p class="eyebrow">Stage 3 of 6<\/p>.*>Open →<\/a>/);
});

test("filter chips press one value at a time and report it", () => {
  const markup = ui.filterChipsMarkup("state", [{ value: "all", label: "All", count: 1200 }, { value: "flagged", label: "Flagged", count: 3 }, { value: "done", label: "Done" }], "flagged", { label: "Show responses" });
  assert.equal(markup, '<div class="filter-chips" role="group" aria-label="Show responses" data-filter-group="state"><button type="button" aria-pressed="false" data-filter="all">All <b>1,200</b></button><button type="button" aria-pressed="true" data-filter="flagged">Flagged <b>3</b></button><button type="button" aria-pressed="false" data-filter="done">Done</button></div>');
  const chips = ["all", "flagged"].map((value) => ({ dataset: { filter: value }, pressed: value === "flagged" ? "true" : "false",
    getAttribute() { return this.pressed; }, setAttribute(_, v) { this.pressed = v; }, closest(selector) { return selector === "[data-filter]" ? this : group; } }));
  const group = { dataset: { filterGroup: "state" }, querySelectorAll: () => chips, closest: () => group };
  let listener;
  const root = { addEventListener: (_, fn) => { listener = fn; }, removeEventListener: (_, fn) => { if (fn === listener) listener = null; } };
  const seen = [];
  const unbind = ui.bindFilterChips(root, "state", (value) => seen.push(value));
  listener({ target: chips[1] });  // already pressed: nothing
  listener({ target: chips[0] });
  assert.deepEqual(seen, ["all"]);
  assert.deepEqual(chips.map((chip) => chip.pressed), ["true", "false"]);
  group.dataset.filterGroup = "other";
  listener({ target: chips[1] });
  assert.deepEqual(seen, ["all"]);
  unbind();
  assert.equal(listener, null);
});

test("action menus are native popovers with menu items that reuse the page's handlers", () => {
  const markup = ui.actionMenuMarkup({ id: "scan-j1", label: "Scan actions", items: [
    { label: "Start over…", data: { scanAction: "start-over" } },
    { label: "Open grading", href: "#grading/p1" },
    { label: "Delete from Inbox…", data: { scanAction: "delete" }, danger: true, disabled: true },
  ] });
  assert.match(markup, /^<div class="action-menu"><button type="button" class="icon-button action-menu-trigger" popovertarget="scan-j1" aria-label="Scan actions" aria-haspopup="menu" aria-expanded="false" title="Scan actions">⋯<\/button><div id="scan-j1" popover class="action-menu-list" role="menu" aria-label="Scan actions">/);
  assert.match(markup, /<button type="button" role="menuitem" data-scan-action="start-over">Start over…<\/button>/);
  assert.match(markup, /<a role="menuitem" href="#grading\/p1">Open grading<\/a>/);
  assert.match(markup, /<button type="button" role="menuitem" class="danger" data-scan-action="delete" disabled>Delete from Inbox…<\/button>/);
  const first = ui.actionMenuMarkup({ items: [] }), second = ui.actionMenuMarkup({ items: [] });
  assert.notEqual(first.match(/popovertarget="([^"]+)"/)[1], second.match(/popovertarget="([^"]+)"/)[1]);
});

test("loading skeletons keep the .loading-state hook and a readable label", () => {
  assert.equal(ui.loadingMarkup("Loading courses…", 2), '<div class="loading-state loading-skeleton" role="status" aria-busy="true"><span class="sr-only">Loading courses…</span><i></i><i></i></div>');
  assert.equal((ui.loadingMarkup("x", 99).match(/<i><\/i>/g) || []).length, 12);
  assert.equal((ui.loadingMarkup().match(/<i><\/i>/g) || []).length, 3);
});

test("wall-clock dates never pass through a time zone", () => {
  assert.equal(ui.wallDate("2026-09-17"), "Thu, Sep 17");
  assert.equal(ui.wallDate("2026-09-17T23:30:00-05:00"), "Thu, Sep 17");
  assert.equal(ui.wallDate("2026-12-31", { year: true }), "Thu, Dec 31, 2026");
  assert.equal(ui.wallDate("soon"), "soon");
  assert.equal(ui.daysFrom("2026-10-22", "2026-09-26"), "in 26 days");
  assert.equal(ui.daysFrom("2026-09-26", "2026-09-26"), "today");
  assert.equal(ui.daysFrom("2026-09-27", "2026-09-26"), "tomorrow");
  assert.equal(ui.daysFrom("2026-09-25", "2026-09-26"), "yesterday");
  assert.equal(ui.daysFrom("2026-09-17", "2026-09-26"), "9 days ago");
  assert.equal(ui.daysFrom("2027-03-14", "2026-11-01"), "in 133 days");  // across a DST change
  assert.equal(ui.daysFrom("x", "2026-09-26"), "");
  // The campus date (America/Chicago) of an instant, whatever the browser's own time zone.
  const zone = process.env.TZ;
  try {
    for (const browserZone of ["America/Chicago", "America/Los_Angeles", "Pacific/Auckland", "UTC"]) {
      process.env.TZ = browserZone;
      assert.equal(ui.localToday(new Date("2026-09-06T04:59:00Z")), "2026-09-05", browserZone);  // 11:59 pm CDT
      assert.equal(ui.localToday(new Date("2026-11-07T06:30:00Z")), "2026-11-07", browserZone);  // 12:30 am CST
      assert.equal(ui.localToday(new Date("2026-11-07T05:30:00Z")), "2026-11-06", browserZone);  // 11:30 pm CDT
    }
  } finally {
    if (zone === undefined) delete process.env.TZ; else process.env.TZ = zone;
  }
});

test("raw state codes have human names", () => {
  assert.deepEqual(["todo", "check", "wait", "done", "READY", "needs_review", "pending", "failed"].map((code) => ui.stateLabel(code)),
    ["Needs you", "Needs you", "Working", "Nothing to do", "Ready to print", "Needs review", "Queued", "Failed"]);
  assert.equal(ui.stateLabel("review_ready"), "Finished");
  assert.equal(ui.stateLabel("review_ready", { waiting: 4 }), "Ready for review");
  assert.equal(ui.stateLabel("partly_graded"), "Partly graded");
  assert.equal(ui.stateLabel(""), "");
});

test("leave guards ask before leaving and restore the address when declined", () => {
  const listeners = {};
  const replaced = [];
  const win = {
    location: { href: "http://x/#grading/p1" },
    history: { state: null, replaceState: (_, __, url) => replaced.push(url) },
    addEventListener: (type, fn) => { listeners[type] = fn; },
  };
  let answer = false; const asked = [];
  ui.installLeaveGuard({ window: win, confirm: (message) => { asked.push(message); return answer; } });
  // Nothing unsaved: navigation proceeds without a question.
  let stopped = false;
  const event = (oldURL) => ({ oldURL, stopImmediatePropagation: () => { stopped = true; } });
  win.location.href = "http://x/#rooms";
  listeners.hashchange(event("http://x/#grading/p1"));
  assert.deepEqual(asked, []);
  const off = ui.registerLeaveGuard(() => "You have unsaved rubric edits.");
  ui.registerLeaveGuard(() => { throw new Error("broken guard"); });
  assert.equal(ui.leaveMessage(), "You have unsaved rubric edits.");
  win.location.href = "http://x/#inbox";
  listeners.hashchange(event("http://x/#rooms"));
  assert.deepEqual(asked, ["You have unsaved rubric edits."]);
  assert.equal(stopped, true);
  assert.deepEqual(replaced, ["http://x/#rooms"]);
  // Accepting lets the view render; unload asks the browser to confirm while work is unsaved.
  answer = true; stopped = false;
  listeners.hashchange(event("http://x/#rooms"));
  assert.equal(stopped, false);
  const unload = { prevented: false, preventDefault() { this.prevented = true; } };
  listeners.beforeunload(unload);
  assert.equal(unload.prevented, true);
  off();
  assert.equal(ui.leaveMessage(), null);
  const clean = { prevented: false, preventDefault() { this.prevented = true; } };
  listeners.beforeunload(clean);
  assert.equal(clean.prevented, false);
  // A dialog intent captured while a view renders an accepted navigation survives that navigation (a Workspace settings link
  // that opens a dialog on the page it lands on): the guard counts the navigation ahead of every view's own listener. A later
  // navigation still abandons the intent, and a declined one (stopped at the guard) does not.
  const priorLocation = globalThis.location;
  globalThis.location = win.location;
  try {
    const dialog = { open: false, firstElementChild: {} };
    win.location.href = "http://x/#inbox";
    listeners.hashchange(event("http://x/#settings"));
    const intended = ui.captureDialogIntent(dialog);
    assert.equal(intended(), true, "the navigation that opened the dialog does not abandon it");
    const declined = ui.registerLeaveGuard(() => "Unsaved.");
    answer = false;
    listeners.hashchange(event("http://x/#inbox"));
    declined();
    assert.equal(intended(), true, "a declined navigation leaves the pending opening alone");
    answer = true;
    // The address reads the same (a redirect back to it): only the guard's count tells the navigation happened.
    listeners.hashchange(event("http://x/#mail"));
    assert.equal(intended(), false, "a later accepted navigation cancels the pending opening");
  } finally {
    if (priorLocation === undefined) delete globalThis.location; else globalThis.location = priorLocation;
  }
});

test("one shortcut registry that never fires while typing, in grids or under other dialogs", () => {
  const doc = { open: null, querySelector(selector) { return selector === "dialog[open]:not(.palette)" ? this.open : null; } };
  const target = (tagName = "BODY", extra = {}) => ({ tagName, isContentEditable: false, closest: () => null, ...extra });
  const key = (key, options = {}) => ({ key, target: target(), defaultPrevented: false, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
    preventDefault() { this.defaultPrevented = true; }, ...options });
  const runs = [];
  let onPage = true;
  const offSlash = ui.registerShortcut({ key: "/", label: "Search this page", when: () => onPage, run: () => runs.push("/") });
  const offHelp = ui.registerShortcut({ key: "?", label: "Keyboard shortcuts", scope: "global", run: () => runs.push("?") });
  const offPalette = ui.registerShortcut({ key: "Mod+K", label: "Search Faculty", allowInInput: true, scope: "global", run: () => runs.push("K") });
  assert.equal(ui.dispatchShortcut(key("/"), doc), true);
  assert.equal(ui.dispatchShortcut(key("?", { shiftKey: true }), doc), true);
  assert.equal(ui.dispatchShortcut(key("k", { ctrlKey: true }), doc), true);
  assert.equal(ui.dispatchShortcut(key("K", { metaKey: true, shiftKey: false }), doc), true);
  assert.deepEqual(runs, ["/", "?", "K", "K"]);
  // Modifiers that were not asked for, typing, grids, other dialogs and handled events are left alone.
  assert.equal(ui.dispatchShortcut(key("/", { ctrlKey: true }), doc), false);
  assert.equal(ui.dispatchShortcut(key("k"), doc), false);
  assert.equal(ui.dispatchShortcut(key("/", { target: target("INPUT") }), doc), false);
  assert.equal(ui.dispatchShortcut(key("k", { ctrlKey: true, target: target("TEXTAREA") }), doc), true);
  assert.equal(ui.dispatchShortcut(key("/", { target: target("DIV", { isContentEditable: true }) }), doc), false);
  assert.equal(ui.dispatchShortcut(key("/", { target: target("TD", { closest: (s) => s === '[role="grid"]' ? {} : null }) }), doc), false);
  assert.equal(ui.dispatchShortcut(key("/", { defaultPrevented: true }), doc), false);
  doc.open = {};
  assert.equal(ui.dispatchShortcut(key("?", { shiftKey: true }), doc), false);
  doc.open = null;
  // Inactive and unregistered shortcuts stop; the newest active registration of a key wins and is listed once.
  onPage = false;
  assert.equal(ui.dispatchShortcut(key("/"), doc), false);
  assert.deepEqual(ui.listShortcuts().map((s) => s.key), ["?", "Mod+K"]);
  const offOther = ui.registerShortcut({ key: "?", label: "Newer", run: () => runs.push("newer") });
  ui.dispatchShortcut(key("?", { shiftKey: true }), doc);
  assert.equal(runs.at(-1), "newer");
  assert.deepEqual(ui.listShortcuts().filter((s) => s.key === "?").map((s) => s.label), ["Newer"]);
  [offOther, offSlash, offHelp, offPalette].forEach((off) => off());
  assert.deepEqual(ui.listShortcuts(), []);
  assert.equal(ui.dispatchShortcut(key("?", { shiftKey: true }), doc), false);
});

test("a shortcut whose when() throws is off: not run, not listed", () => {
  const doc = { querySelector: () => null };
  const target = { tagName: "BODY", isContentEditable: false, closest: () => null };
  const key = (key) => ({ key, target, defaultPrevented: false, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, preventDefault() { this.defaultPrevented = true; } });
  const runs = [];
  const off = ui.registerShortcut({ key: "/", label: "Search this page", when: () => { throw new Error("page gone"); }, run: () => runs.push("/") });
  assert.equal(ui.dispatchShortcut(key("/"), doc), false);
  assert.deepEqual(ui.listShortcuts(), []);
  assert.deepEqual(runs, []);
  off();
});

test("Alt+letter shortcuts also match by physical key (macOS Option types another character); grouped keys list once", () => {
  const doc = { querySelector: () => null };
  const key = (key, options = {}) => ({ key, code: "", target: { tagName: "BODY", isContentEditable: false, closest: () => null }, defaultPrevented: false,
    ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, preventDefault() { this.defaultPrevented = true; }, ...options });
  const runs = [];
  const offs = [ui.registerShortcut({ key: "Alt+A", label: "Approve", run: () => runs.push("A") })];
  assert.equal(ui.dispatchShortcut(key("å", { code: "KeyA", altKey: true }), doc), true);
  assert.equal(ui.dispatchShortcut(key("a", { code: "KeyA", altKey: true }), doc), true);
  // Without Alt the physical key does not stand in, and another letter's key never matches.
  assert.equal(ui.dispatchShortcut(key("å", { code: "KeyA" }), doc), false);
  assert.equal(ui.dispatchShortcut(key("π", { code: "KeyP", altKey: true }), doc), false);
  assert.deepEqual(runs, ["A", "A"]);
  const digits = { key: "1–9", label: "Tick that rubric line" };
  for (let digit = 1; digit <= 3; digit++) offs.push(ui.registerShortcut({ key: String(digit), label: `Line ${digit}`, group: digits, run: () => runs.push(digit) }));
  offs.push(ui.registerShortcut({ key: "0", label: "Lines 10–19", run: () => runs.push(0) }));
  assert.deepEqual(ui.listShortcuts().map((s) => s.key), ["Alt+A", "1–9", "0"]);
  assert.equal(ui.dispatchShortcut(key("2"), doc), true);
  assert.equal(runs.at(-1), 2);
  offs.forEach((off) => off());
  assert.deepEqual(ui.listShortcuts(), []);
});

test("whenVisible runs a poll now in a shown tab, and once when a hidden tab is shown again", () => {
  const listeners = new Set();
  const doc = { hidden: false, addEventListener: (_, f) => listeners.add(f), removeEventListener: (_, f) => listeners.delete(f) };
  const show = (hidden) => { doc.hidden = hidden; [...listeners].forEach((f) => f()); };
  let runs = 0;
  ui.whenVisible(() => { runs += 1; }, doc);
  assert.equal(runs, 1);
  assert.equal(listeners.size, 0);
  doc.hidden = true;
  ui.whenVisible(() => { runs += 1; }, doc);
  assert.equal(runs, 1, "a hidden tab reads nothing");
  show(true);
  assert.equal(runs, 1);
  show(false);
  assert.equal(runs, 2);
  assert.equal(listeners.size, 0, "the listener goes with its one run");
  show(true); show(false);
  assert.equal(runs, 2);
});

test("a module that cannot be loaded shows the updated notice once and rethrows", async () => {
  const prepended = [];
  const made = { innerHTML: "", get firstElementChild() { return { querySelector: () => ({ addEventListener() {} }), html: made.innerHTML }; } };
  globalThis.document = { querySelector: (selector) => selector === "main" ? { prepend: (node) => prepended.push(node) } : null, createElement: () => made };
  try {
    const loaded = await ui.loadModule(async () => ({ ok: 1 }));
    assert.equal(loaded.ok, 1);
    await assert.rejects(ui.loadModule(async () => { throw new TypeError("Failed to fetch dynamically imported module"); }), TypeError);
    await assert.rejects(ui.loadModule(async () => { throw new TypeError("again"); }), TypeError);
    assert.equal(prepended.length, 1);
    assert.match(prepended[0].html, /Faculty was updated since this tab opened/);
    assert.match(prepended[0].html, /data-app-reload=""/);
  } finally {
    delete globalThis.document;
  }
});

test("ui.js keeps its helpers pure markup: no inline style attributes", () => {
  assert.doesNotMatch(source, /style="/);
});

// ---- Toast v2 and the page header (W1b) ------------------------------------------------------------------------------
const header = await import(new URL("../static/page-header.js", import.meta.url).href);

test("toastSpec keeps toast(message) and toast(message, true) and adds tones, actions and persistence", () => {
  const plain = ui.toastSpec("Saved.");
  assert.equal(plain.tone, "success"); assert.equal(plain.persist, false); assert.equal(plain.timeout, 4000); assert.equal(plain.role, "status");
  const error = ui.toastSpec("Failed.", true);
  assert.equal(error.tone, "danger"); assert.equal(error.persist, true); assert.equal(error.timeout, 0); assert.equal(error.role, "alert");
  const run = () => {};
  const restore = ui.toastSpec("Deleted from the Inbox", { tone: "success", action: { label: "Restore", run } });
  assert.deepEqual(restore.action, { label: "Restore", run }); assert.equal(restore.persist, false); assert.equal(restore.timeout, 8000);
  assert.equal(ui.toastSpec("x", { action: { label: "No handler" } }).action, null);
  assert.equal(ui.toastSpec("x", { tone: "warning", persist: true }).persist, true);
  assert.equal(ui.toastSpec("x", { tone: "bogus" }).tone, "success");
  assert.equal(ui.toastSpec("x", { error: true }).tone, "danger");
  // { tone: "error" } is the danger tone: a persistent alert, never a green success toast.
  const err = ui.toastSpec("x", { tone: "error" });
  assert.equal(err.tone, "danger"); assert.equal(err.persist, true); assert.equal(err.role, "alert");
  assert.equal(ui.toastSpec("  spaced  ").message, "spaced");
});

test("routePath is the hash's path without its query", () => {
  assert.equal(header.routePath("#grading/abc?tab=review"), "grading/abc");
  assert.equal(header.routePath("inbox"), "inbox");
  assert.equal(header.routePath(""), "");
  assert.equal(header.routePath("#overview?operations"), "overview");
});

test("crumbsMarkup links every crumb but the page, and escapes labels", () => {
  const markup = header.crumbsMarkup([{ label: "Home", href: "#overview" }, { label: "Grading", href: "#grading" }, { label: "Test <1>" }], "<svg></svg>");
  assert.equal(markup, '<ol><li><a href="#overview"><svg></svg><span>Home</span></a></li><li><a href="#grading"><span>Grading</span></a></li><li aria-current="page"><span>Test &lt;1&gt;</span></li></ol>');
  assert.equal(header.crumbsMarkup([{ label: "Home", href: "#overview" }]), '<ol><li aria-current="page"><span>Home</span></li></ol>');
  assert.equal(header.crumbsMarkup([]), "<ol></ol>");
});

test("tabTitle counts attention and names the workspace", () => {
  assert.equal(header.tabTitle("Rooms", 0, ""), "Rooms · Faculty");
  assert.equal(header.tabTitle("Test 1", 3, "Fall teaching"), "(3) Test 1 · Fall teaching");
  assert.equal(header.tabTitle("", 0, "  "), "Faculty · Faculty");
});

test("resolveHeader lets a view override only what it names", () => {
  const defaults = { variant: "compact", title: "Grading", subtitle: "Read work.", crumbs: [{ label: "Home", href: "#overview" }, { label: "Grading", href: "#grading" }, { label: "Assignment" }], action: { label: "Add" } };
  assert.equal(header.resolveHeader(defaults, null).title, "Grading");
  const merged = header.resolveHeader(defaults, { title: "Test 1", subtitle: undefined, action: null });
  assert.equal(merged.title, "Test 1");
  assert.equal(merged.subtitle, "Read work.");
  assert.equal(merged.action, null);
  assert.deepEqual(merged.crumbs.at(-1), { label: "Test 1" });
  const own = header.resolveHeader(defaults, { title: "Test 1", crumbs: [{ label: "Home" }, { label: "Exams" }] });
  assert.deepEqual(own.crumbs, [{ label: "Home" }, { label: "Exams" }]);
  assert.deepEqual(header.resolveHeader(null, null).secondary, []);
});

test("attentionBadges maps the server's attention cards to sidebar entries and Home's health dot", () => {
  const result = header.attentionBadges([
    { href: "#inbox", count: 3, derived: true },
    { href: "#exams", count: 1 },
    { href: "#inbox/job/x", count: 2, derived: true },
    { href: "#grading?tab=rubrics", count: 4 },
    { href: "#overview?operations", count: 5 },
    { href: "#response-cards", count: 0 },
    { href: "#unknown", count: 9 },
    { href: "#inboxes", count: 7 },
  ]);
  assert.deepEqual(result.counts, { inbox: 5, exams: 1, grading: 4 });
  assert.equal(result.health, 5);
  assert.equal(result.total, 10);
  assert.deepEqual(header.attentionBadges(null), { counts: {}, health: 0, total: 0 });
  // RECORDINGS "ONLY WHAT IS FACULTY'S IS KEPT": the recordings that wait for Keep or Don't keep count on the Recordings entry.
  assert.deepEqual(header.attentionBadges([{ kind: "review", key: "recordings:keep", href: "#recordings", count: 2 }]), { counts: { recordings: 2 }, health: 0, total: 2 });
});

test("an Inbox card counted from stored statuses gets no badge and no tab-title count", () => {
  // 23 scans stored as needs_review, which the Inbox itself files as Completed: the badge must not claim them.
  const stored = header.attentionBadges([{ kind: "review", label: "Submissions to review", count: 23, href: "#inbox" },
    { kind: "error", label: "Teaching Inbox needs attention", count: 2, href: "#inbox" }]);
  assert.deepEqual(stored, { counts: {}, health: 0, total: 0 });
  const derived = header.attentionBadges([{ kind: "review", label: "Scans and submissions that need you", count: 1, href: "#inbox", derived: true }]);
  assert.deepEqual(derived, { counts: { inbox: 1 }, health: 0, total: 1 });
});

test("a card counted in another card adds nothing to the badge or the tab title (TEACHING-WORKFLOWS NEEDS ME COUNT)", () => {
  // 2026-10-06: a wellbeing concern on a conversation that needs a reply made the Follow-up badge 3 over a Needs me tab of 2.
  const result = header.attentionBadges([
    { key: "support:wellbeing", href: "#follow-ups?filter=wellbeing", count: 1, counted_in: "support:followup" },
    { key: "support:followup", href: "#follow-ups", count: 2 },
    { key: "support:appointment", href: "#office-hours", count: 1 },
  ]);
  assert.deepEqual(result, { counts: { "follow-ups": 3 }, health: 0, total: 3 });
});

test("page-header.js writes no inline style attributes", async () => {
  const text = await readFile(new URL("../static/page-header.js", import.meta.url), "utf8");
  assert.doesNotMatch(text, /style="/);
});

test("a paint with unchanged data rewrites no header button and no tab title; a clicked button is written again", () => {
  // Enough of a document for createHeader. innerHTML reads back as a browser serializes it (`<path/>` → `<path></path>`),
  // which is why the painter compares with the markup it last wrote, not with innerHTML.
  const writes = [];
  class FakeElement {
    constructor(name) { this.name = name; this.dataset = {}; this.hidden = false; this.disabled = false; this.text = ""; this.html = ""; }
    get innerHTML() { return this.html.replace(/<path([^>]*)\/>/g, "<path$1></path>"); }
    set innerHTML(value) { this.html = value; writes.push(this.name); }
    get textContent() { return this.text; }
    set textContent(value) { this.text = value; }
    closest(selector) { return this.selectors.includes(selector) ? this : this.parent?.closest(selector) ?? null; }
  }
  class FakeButton extends FakeElement {}
  const primary = Object.assign(new FakeButton("primary"), { selectors: ["#primary-action"] });
  const host = Object.assign(new FakeElement("secondary"), { selectors: [".page-heading-secondary"] });
  const nodes = { "#primary-action": primary, ".page-heading-secondary": host, "#page-title": new FakeElement("title"), "#breadcrumb": new FakeElement("crumbs") };
  let clicked = null, title = "", titles = 0;
  const doc = {
    querySelector: (selector) => nodes[selector] || null,
    addEventListener: (type, listener) => { if (type === "click") clicked = listener; },
    get title() { return title; },
    set title(value) { title = value; titles += 1; },
  };
  const saved = { Element: globalThis.Element, HTMLElement: globalThis.HTMLElement, HTMLButtonElement: globalThis.HTMLButtonElement };
  Object.assign(globalThis, { Element: FakeElement, HTMLElement: FakeElement, HTMLButtonElement: FakeButton });
  try {
    const where = { hash: "#rooms" };
    const painter = header.createHeader({ doc, icon: (name) => `<svg class="icon "><path d="M0 0" data-name="${name}"/></svg>`, location: where });
    let runs = 0;
    const action = { label: "Add a room", icon: "plus", action: "add-room" };
    const spec = { route: "#rooms", title: "Rooms", crumbs: [{ label: "Home", href: "#overview" }, { label: "Rooms" }], secondary: [{ label: "Settings", icon: "gear", run: () => { runs += 1; } }] };
    painter.defaults({ ...spec, action });
    assert.deepEqual(writes.filter((name) => name !== "crumbs"), ["primary", "secondary"]);
    assert.equal(titles, 1);
    assert.equal(primary.dataset.action, "add-room");
    writes.length = 0;
    painter.setTitleParts({ count: 0, name: "" });
    painter.defaults({ ...spec, action: { ...action, disabled: true } });
    assert.deepEqual(writes, []);
    assert.equal(titles, 1);
    assert.equal(primary.disabled, true, "disabled and data-* are written on every paint");
    painter.setTitleParts({ count: 2 });
    assert.equal(title, "(2) Rooms · Faculty");
    assert.equal(titles, 2);
    painter.defaults({ ...spec, action: { ...action, label: "Add rooms" } });
    assert.deepEqual(writes, ["primary"]);
    // A click's handler may rewrite its button; the next paint writes the header's markup again, as it always did.
    writes.length = 0;
    const settings = Object.assign(new FakeButton("settings button"), { selectors: ["[data-header-secondary]"], parent: host, dataset: { headerSecondary: "0" } });
    clicked({ target: settings });
    assert.equal(runs, 1);
    primary.disabled = false;
    clicked({ target: primary });
    painter.setTitleParts({ count: 2 });
    assert.deepEqual(writes, ["primary", "secondary"]);
  } finally {
    Object.assign(globalThis, saved);
  }
});

// ---- W1c: system status, completion notices and the command palette (pure parts) --------------------------------------
const status = await import(new URL("../static/system-status.js", import.meta.url).href);
const palette = await import(new URL("../static/palette.js", import.meta.url).href);

const overviewFixture = (overrides = {}) => ({
  generated_at: "t1",
  attention: [],
  recent_scans: [],
  operations: {
    worker: { state: "active", heartbeat_at: "2026-09-26T04:55:44Z" },
    totals: { running: 0, pending: 0, failed: 0, needs_review: 0 },
    storage: { backup: { status: "complete", verified_at: "2026-09-26T02:16:06Z", error_count: 0 }, disks: [] },
    queues: [{ key: "frq_grading", label: "FRQ grading", href: "#grading", running: 0, pending: 0, failed: 0, needs_review: 0 }],
  },
  ...overrides,
});

test("statusSummary says what the rail's status button shows", () => {
  assert.equal(status.statusSummary(null, { online: null }).label, "Connecting to server…");
  assert.equal(status.statusSummary(overviewFixture(), { online: false }).label, "Server unavailable");
  assert.equal(status.statusSummary(overviewFixture(), { online: true }).label, "Connected · all clear");
  const busy = overviewFixture();
  busy.operations.totals.running = 2;
  assert.equal(status.statusSummary(busy, { online: true }).label, "Connected · 2 running");
  const unhealthy = overviewFixture({ attention: [{ href: "#overview?operations", count: 1, label: "Check storage and backup" }] });
  assert.deepEqual(status.statusSummary(unhealthy, { online: true }), { label: "Connected · check health", tone: "warning" });
});

test("backupLabel never calls an incomplete backup verified", () => {
  assert.match(status.backupLabel({ status: "complete", verified_at: "2026-09-26T02:16:06Z" }), /^Verified /);
  assert.equal(status.backupLabel({ status: "not_run", verified_at: null }), "No verified backup recorded");
  assert.equal(status.backupLabel({ status: "running", verified_at: null }), "Backup running · not verified");
  assert.equal(status.backupLabel({ status: "interrupted", verified_at: null }), "Backup interrupted · not verified");
});

test("statusPanelMarkup escapes server text and links running work", () => {
  const data = overviewFixture();
  data.operations.queues[0].running = 1;
  data.operations.queues.push({ key: "x", label: "<b>odd</b>", href: "#inbox", running: 0, pending: 3, failed: 0 });
  const html = status.statusPanelMarkup(data, { online: true, email: true, updated: "Updated just now" }, ui.escape);
  assert.match(html, /<a href="#grading"><span>FRQ grading<\/span><small>1 running<\/small><\/a>/);
  assert.match(html, /&lt;b&gt;odd&lt;\/b&gt;/);
  assert.match(html, /Email gateway<\/dt><dd><strong>Configured/);
  assert.doesNotMatch(html, /style=/);
  const idle = status.statusPanelMarkup(overviewFixture(), { online: true, email: false, updated: "" }, ui.escape);
  assert.match(idle, /Nothing is running or queued\./);
  assert.match(idle, /Not configured/);
});

test("transitions tell what changed between two overview reads, and nothing on the first", () => {
  const stages = [{ key: "scan", label: "Scan", title: "Check the scanned pages", state: "done", status: "Done", page: "pages", detail: "" },
    { key: "students", label: "Students", title: "Find whose paper each is", state: "started", status: "To do", page: "students", detail: "Find them.", urgent: true }];
  const scan = (state) => ({ id: "j1", href: "#inbox/job/j1", created_at: "2026-10-07T15:40:49.862408+00:00", scan: { headline: "Quiz 2 · papers without labels", state, stages } });
  const before = overviewFixture({ generated_at: "a", recent_scans: [scan("wait")] });
  before.operations.queues[0].running = 3;
  const after = overviewFixture({ generated_at: "b", recent_scans: [scan("todo")], attention: [{ href: "#response-cards", label: "Response card email needs attention", count: 2 }] });
  assert.deepEqual(status.transitions(null, after), []);
  assert.deepEqual(status.transitions(before, { ...before }), []);
  const notices = status.transitions(before, after);
  assert.deepEqual(notices.map((notice) => [notice.message, notice.label, notice.href]), [
    ["Quiz 2 · papers without labels: ready for you", "Students", "#inbox/job/j1/students"],
    ["FRQ grading: finished", "Open", "#grading"],
    ["Response card email needs attention (2)", "Open", "#response-cards"],
  ]);
  // A notice pointing at the page on screen is left out; at most `limit` are given.
  assert.equal(status.transitions(before, after, { here: "#inbox/job/j1/students" }).length, 2);
  assert.equal(status.transitions(before, after, { limit: 1 }).length, 1);
  // A new scan is announced unless the Inbox is on screen.
  const later = { id: "j2", href: "#inbox/job/j2", created_at: "2026-10-07T16:02:49.818505+00:00", scan: { headline: "Test 1", state: "wait", stages: [] } };
  const arrived = overviewFixture({ generated_at: "c", recent_scans: [later, scan("wait")] });
  assert.equal(status.transitions(before, arrived)[0].message, "New scan received: Test 1");
  assert.equal(status.transitions(before, arrived, { here: "#inbox" }).filter((notice) => notice.message.startsWith("New scan")).length, 0);
  // The list is the most recent few: deleting a scan (or restoring an older one) brings an old scan into it, which is not new.
  const older = { id: "j0", href: "#inbox/job/j0", created_at: "2026-09-29T21:23:33+00:00", scan: { headline: "Quiz 3", state: "todo", stages: [] } };
  const deleted = overviewFixture({ generated_at: "d", recent_scans: [scan("wait"), older] });
  assert.deepEqual(status.transitions(arrived, deleted).filter((notice) => notice.message.startsWith("New scan")), []);
  const restored = overviewFixture({ generated_at: "e", recent_scans: [later, scan("wait"), older] });
  assert.deepEqual(status.transitions(arrived, restored).filter((notice) => notice.message.startsWith("New scan")), []);
  // More failures than before is a warning.
  const failed = overviewFixture({ generated_at: "d" });
  failed.operations.queues[0].failed = 2;
  assert.deepEqual(status.transitions(overviewFixture({ generated_at: "c" }), failed).map((notice) => [notice.message, notice.tone]), [["FRQ grading: 2 failed", "warning"]]);
});

test("ago counts seconds and minutes", () => {
  assert.equal(status.ago(0), "not yet");
  assert.equal(status.ago(1000, 3000), "just now");
  assert.equal(status.ago(1000, 13000), "12 s ago");
  assert.equal(status.ago(0 + 1, 1 + 180000), "3 min ago");
});

function memoryStorage() {
  const data = new Map();
  return { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, String(value)) };
}

test("recent places keep one entry per detail page, newest first, at most eight", () => {
  const storage = memoryStorage();
  assert.deepEqual(palette.recentPlaces(storage), []);
  palette.rememberPlace({ hash: "#rooms", label: "Rooms" }, storage);
  assert.deepEqual(palette.recentPlaces(storage), [], "list pages are always in Pages");
  palette.rememberPlace({ hash: "#grading/a", label: "Test 1" }, storage);
  palette.rememberPlace({ hash: "#rooms/r1", label: "HECC 105" }, storage);
  palette.rememberPlace({ hash: "#grading/a?tab=review", label: "Test 1" }, storage);
  assert.deepEqual(palette.recentPlaces(storage).map((place) => place.hash), ["#grading/a?tab=review", "#rooms/r1"]);
  // One scan's stage pages, one plan's steps and one gradebook's pages are one place each.
  palette.rememberPlace({ hash: "#inbox/job/j1", label: "Unrecognized scan" }, storage);
  palette.rememberPlace({ hash: "#inbox/job/j1/papers", label: "Unrecognized scan", detail: "Papers" }, storage);
  palette.rememberPlace({ hash: "#grading/a/whatever", label: "Test 1" }, storage);
  assert.deepEqual(palette.recentPlaces(storage).map((place) => place.hash), ["#grading/a/whatever", "#inbox/job/j1/papers", "#rooms/r1"]);
  assert.equal(palette.placeKey("gradebook/t1/setup"), "gradebook/t1");
  assert.equal(palette.placeKey("course/c1/section/507"), "course/c1/section/507");
  for (let index = 0; index < 12; index += 1) palette.rememberPlace({ hash: `#seating/c${index}`, label: `Chart ${index}` }, storage);
  assert.equal(palette.recentPlaces(storage).length, 8);
  const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.deepEqual(palette.recentPlaces(broken), []);
  assert.doesNotThrow(() => palette.rememberPlace({ hash: "#rooms/r1", label: "HECC 105" }, broken));
});

test("recent places survive a browser that blocks site data (the localStorage getter throws)", () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new Error("SecurityError: The operation is insecure."); } });
  try {
    assert.deepEqual(palette.recentPlaces(), []);
    assert.doesNotThrow(() => palette.rememberPlace({ hash: "#rooms/r1", label: "HECC 105" }));
  } finally {
    if (descriptor) Object.defineProperty(globalThis, "localStorage", descriptor);
    else delete globalThis.localStorage;
  }
});

test("matchItems finds every word in any order, best matches and their group first", () => {
  const items = [
    { group: "Pages", label: "Rooms", href: "#rooms" },
    { group: "Pages", label: "Response Cards", href: "#response-cards" },
    { group: "Grading", label: "Test 1", detail: "MATH 140 · Fall 2026 · Grading", href: "#grading/a" },
    { group: "Sections", label: "MATH 140 · Fall 2026 · Section 507", href: "#course/x/section/507" },
  ];
  assert.equal(palette.matchItems(items, "").length, 4);
  assert.deepEqual(palette.matchItems(items, "507 math").map((item) => item.label), ["MATH 140 · Fall 2026 · Section 507"]);
  assert.deepEqual(palette.matchItems(items, "fall test").map((item) => item.label), ["Test 1"]);
  assert.deepEqual(palette.matchItems(items, "cards").map((item) => item.label), ["Response Cards"]);
  // A label that starts with the query comes before one that merely contains it, whatever the list order.
  assert.deepEqual(palette.matchItems([{ group: "Pages", label: "Latest test" }, ...items], "test").map((item) => item.label), ["Test 1", "Latest test"]);
  assert.deepEqual(palette.matchItems(items, "résponse").map((item) => item.label), ["Response Cards"]);
  // Groups stay together: every Pages hit before the Sections hit when a page matches best.
  const grouped = palette.matchItems(items, "a");
  assert.deepEqual([...new Set(grouped.map((item) => item.group))], grouped.reduce((groups, item) => (groups.at(-1) === item.group ? groups : [...groups, item.group]), []));
});

test("a number in the query matches from a number's first digit, so a quiz number does not match every course code (rest-shell-10)", () => {
  const items = [
    { group: "Grading", label: "Quiz 2", detail: "MATH 140 · Fall 2026 · Grading", href: "#grading/q2" },
    { group: "Grading", label: "Quiz 4", detail: "MATH 140 · Fall 2026 · Grading", href: "#grading/q4" },
    { group: "Sections", label: "MATH 140 · Fall 2026 · Section 507", detail: "Business Math", keywords: "gradebook grades scores", href: "#course/x/section/507" },
    { group: "Sections", label: "Roster · MATH 140 · Fall 2026 · Section 507", detail: "Business Math", keywords: "roster students", href: "#course/x/section/507/roster" },
  ];
  assert.deepEqual(palette.matchItems(items, "quiz 4").map((item) => item.label), ["Quiz 4"]);
  assert.deepEqual(palette.matchItems(items, "quiz 4 grading").map((item) => item.label), ["Quiz 4"]);
  assert.deepEqual(palette.matchItems(items, "507 gradebook").map((item) => item.href), ["#course/x/section/507"]);
  assert.deepEqual(palette.matchItems(items, "gradebook 507").map((item) => item.href), ["#course/x/section/507"]);
  assert.deepEqual(palette.matchItems(items, "roster 507").map((item) => item.href), ["#course/x/section/507/roster"]);
  assert.deepEqual(palette.matchItems(items, "50").map((item) => item.label).length, 2, "a number's beginning still finds it");
  assert.deepEqual(palette.matchItems(items, "40"), [], "never the middle of one");
  assert.equal(palette.matchItems(items, "140").length, 4);
});

test("entityItems builds links from the existing endpoints' answers only", () => {
  const items = palette.entityItems({
    courseGroups: [{ id: "g", code: "MATH 140", term: "Fall", year: 2026, name: "Business Math", sections: [507], is_current: true }],
    courseHref: (group, section) => (section ? `#course/g/section/${section}` : "#course/g/home"),
    catalog: { assignments: [{ id: "a1", title: "Test 1", code: "MATH 140", definition_id: "d 1" }, { id: "a2", title: "Quiz 4", code: "MATH 140", definition_id: "d 1" },
      { id: "a3", title: "Homework 9", code: "MATH 140", definition_id: "d 1" }], sections: [],
      lifecycle: [{ assignment_id: "a1", term_id: "t1", term_label: "Fall 2026", exam_event_id: "e/1", active: true },
        { assignment_id: "a2", term_id: "t 1", term_label: "Fall 2026", exam_event_id: null, active: true },
        { assignment_id: "a3", term_id: "t1", term_label: "Fall 2026", exam_event_id: null, active: false }] },
    grading: { assignments: [{ id: "p1", title: "Quiz 2", code: "MATH 140", term: "Other", term_other: "Winter", year: 2026 }] },
    seating: [{ id: "c1", title: "Test 1", room_name: "HECC 105", code: "MATH 140", term_label: "Fall 2026", sections: [507, 508] }],
    rooms: [{ id: "r/1", name: "HECC 105" }],
    gradebook: { terms: [{ id: "t1", code: "MATH 140", term_label: "Fall 2026", name: "Business Math" }] },
    inbox: { jobs: [{ id: "j1", scan: { headline: "Quiz 2 · papers without labels", facts: ["650 pages"] } }, { id: "j2", action: "roster.sync" }] },
  });
  const byGroup = Object.fromEntries(items.map((item) => [item.group + ":" + item.label, item.href]));
  assert.equal(byGroup["Courses:MATH 140 · Fall 2026"], "#course/g/home");
  assert.equal(byGroup["Sections:MATH 140 · Fall 2026 · Section 507"], "#course/g/section/507");
  assert.equal(byGroup["Canvas:Canvas content · MATH 140 · Fall 2026"], "#course/g/home/canvas");
  assert.equal(byGroup["Canvas:Canvas · MATH 140 · Fall 2026 · Section 507"], "#course/g/section/507/canvas");
  // Assignments are one entry each (tests/test_palette_assignments.mjs); there are no Exams or Grading groups.
  assert.equal(items.filter((item) => ["Exams", "Grading"].includes(item.group)).length, 0);
  // A section's roster is its own place; the section tab (its gradebook) is found by "gradebook".
  assert.equal(byGroup["Sections:Roster · MATH 140 · Fall 2026 · Section 507"], "#course/g/section/507/roster");
  // A section's class schedule (SCHEDULING "CLASS SCHEDULE") is a place of its own, found by "schedule", "due" or "final".
  assert.equal(byGroup["Sections:Class schedule · MATH 140 · Fall 2026 · Section 507"], "#course/g/section/507/schedule");
  assert.match(items.find((item) => item.label === "Class schedule · MATH 140 · Fall 2026 · Section 507").keywords, /schedule.*due.*final/);
  assert.match(items.find((item) => item.label === "MATH 140 · Fall 2026 · Section 507").keywords, /gradebook/);
  assert.equal(byGroup["Assignments:Quiz 2"], "#grading/p1", "a plan without its assignment opens its grading page");
  assert.equal(byGroup["Seating charts:Test 1 · HECC 105"], "#seating/c1");
  assert.equal(byGroup["Rooms:HECC 105"], "#rooms/r%2F1");
  assert.equal(byGroup["Gradebooks:MATH 140 · Fall 2026"], "#gradebook/t1");
  assert.equal(byGroup["Scans:Quiz 2 · papers without labels"], "#inbox/job/j1");
  assert.equal(items.filter((item) => item.group === "Scans").length, 1, "only scans are listed from the Inbox");
});

test("the palette and status modules write no inline style attributes", async () => {
  for (const name of ["palette.js", "system-status.js"]) {
    const text = await readFile(new URL(`../static/${name}`, import.meta.url), "utf8");
    assert.doesNotMatch(text, /style="/, name);
  }
});

test("HEADER LABEL (DESIGN-LANGUAGE §5, Ledger): the label leaves out what the trail and the clock already say", () => {
  const crumbs = [{label: "Home"}, {label: "Courses"}, {label: "MATH 140 · Fall 2026"}, {label: "Section 507"}, {label: "Gradebook"}];
  assert.deepEqual(header.plateLabel({context: "MATH 140 · Fall 2026 · Gradebook", crumbs}), {text: "", date: false});
  assert.deepEqual(header.plateLabel({context: "MATH 140 · Fall 2026", crumbs: [{label: "Home"}, {label: "Assignments"}, {label: "Test 1"}]}), {text: "MATH 140 · Fall 2026", date: false});
  assert.deepEqual(header.plateLabel({label: "Campus date · Sun, Oct 4", crumbs: [{label: "Home"}, {label: "Calendar"}]}), {text: "Campus date · Sun, Oct 4", date: false});
  // Anything new keeps the label whole (never a fragment like "every term").
  assert.deepEqual(header.plateLabel({context: "MATH 140 · every term", crumbs}), {text: "MATH 140 · every term", date: false});
  // No page prints the date: the clock beside the trail shows it.
  assert.deepEqual(header.plateLabel({crumbs: [{label: "Home"}, {label: "Mail"}]}), {text: "", date: false});
});

test("a capture focus hold protects only its editor, and only while focus stays in an assistant surface (GRADEBOOK)", () => {
  const listeners = {};
  const doc = { documentElement: { dataset: {} }, activeElement: null,
    addEventListener(type, run) { (listeners[type] ||= new Set()).add(run); }, removeEventListener(type, run) { listeners[type]?.delete(run); } };
  const fire = (type, target) => { const event = { type, target, stopped: false, stopImmediatePropagation() { this.stopped = true; } }; for (const run of [...(listeners[type] || [])]) run(event); return event; };
  const node = (surface = false) => ({ nodeType: 1, isConnected: true, blurs: 0, closest: () => (surface ? {} : null),
    contains(other) { return other === this; }, focus() { doc.activeElement = this; }, dispatchEvent(event) { if (event.type === "blur") this.blurs += 1; } });
  const saved = Object.getOwnPropertyDescriptor(globalThis, "document");
  Object.defineProperty(globalThis, "document", { configurable: true, value: doc });
  try {
    const editor = node(), chat = node(true), cell = node();
    const release = ui.holdCaptureFocus(editor);
    assert.equal(ui.isCaptureHeld(editor), true); assert.equal(ui.isCaptureHeld(cell), false);
    assert.equal(doc.documentElement.dataset.assistantCaptureActive, "true");
    doc.activeElement = chat;
    assert.equal(fire("blur", editor).stopped, true, "the editor's blur waits while focus is in the chat");
    fire("focusin", chat); fire("pointerdown", chat); assert.equal(ui.isCaptureHeld(editor), true);
    // Clicking another cell ends the hold and delivers the deferred blur once, so the owner commits.
    fire("pointerdown", cell);
    assert.equal(ui.isCaptureHeld(editor), false); assert.equal(editor.blurs, 1);
    assert.equal(doc.documentElement.dataset.assistantCaptureActive, undefined);
    assert.equal(fire("blur", editor).stopped, false);
    release(); assert.equal(doc.activeElement, chat, "an ended hold never takes focus back");
    const again = ui.holdCaptureFocus(editor); doc.activeElement = chat; fire("focusin", chat); again(); assert.equal(doc.activeElement, editor);
    const handed = ui.holdCaptureFocus(editor); doc.activeElement = chat; editor.blurs = 0; handed({ focus: false });
    assert.equal(editor.blurs, 1); assert.equal(doc.activeElement, chat);
    const tabbed = ui.holdCaptureFocus(editor); fire("focusin", cell); assert.equal(ui.isCaptureHeld(editor), false); tabbed();
  } finally {
    if (saved) Object.defineProperty(globalThis, "document", saved); else delete globalThis.document;
  }
});

test("scripted scrolling is smooth unless the visitor prefers reduced motion", () => {
  const saved = Object.getOwnPropertyDescriptor(globalThis, "matchMedia");
  const asked = [];
  let reduce = true;
  globalThis.matchMedia = (query) => { asked.push(query); return { matches: reduce }; };
  try {
    assert.equal(ui.scrollBehavior(), "auto");
    reduce = false;
    assert.equal(ui.scrollBehavior(), "smooth");
    assert.deepEqual(asked, ["(prefers-reduced-motion: reduce)", "(prefers-reduced-motion: reduce)"]);
  } finally {
    if (saved) Object.defineProperty(globalThis, "matchMedia", saved); else delete globalThis.matchMedia;
  }
});
