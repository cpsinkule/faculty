/**
 * A course term's Term schedule (SCHEDULING "TERM SCHEDULE", stage 1c): `#course/<group>/home/schedule` renders into
 * [data-term-schedule-host]. The term's dates, each section's class meetings, and the instructor's office and meeting hours, saved as
 * one revision. Before the first revision, "Import from your calendar" proposes one from the per-date events already there. Nothing
 * reads the schedule until the term switches to it (stage 1d), so the page says so.
 */
import { calloutMarkup, emptyMarkup, errorMessage, loadingMarkup, registerLeaveGuard, runFormAction, wallDate, captureDialogIntent, dialogSession } from "./ui.js";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const LEAVE_QUESTION = "Leave the Term schedule without saving your changes?";

/** @param {{api: any, escape: (v: any) => string, icon: (name: string) => string, toast: any, header?: any,
 *  showDialog?: (html: string) => void, dialogHeader?: (t: string, s?: string) => string}} deps */
export function createTermSchedule({ api, escape, icon, toast, header = null, showDialog = () => {}, dialogHeader = (t) => t }) {
  const e = escape;
  /** @type {any} */ let state = null; // {groupId, view, draft, request, saving}
  let generation = 0;
  const host = () => /** @type {HTMLElement|null} */ (document.querySelector("[data-term-schedule-host]"));
  const currentPage = () => {
    const owner = state, route = location.hash, node = host();
    return () => state === owner && location.hash === route && host() === node && Boolean(node?.isConnected);
  };
  const packed = (draft) => JSON.stringify({ first_day: draft.first_day, last_day: draft.last_day, entries: draft.entries.map(clean), days_off: [...(draft.days_off || [])].sort() });
  const dirty = () => Boolean(state?.view && state.draft && packed(state.draft) !== packed(fromView(state.view)));
  registerLeaveGuard(() => (dirty() && host() ? LEAVE_QUESTION : null));

  /** What the server keeps of an entry (and its identity when it has one). @param {any} entry */
  function clean(entry) {
    const out = { kind: entry.kind, weekdays: [...entry.weekdays].sort(), start: entry.start, end: entry.end, room: (entry.room || "").trim(),
      seats_makeup: entry.kind === "office_hours" ? Boolean(entry.seats_makeup) : entry.kind === "meeting_hours" };
    if (entry.entry) out.entry = entry.entry;
    if (entry.kind === "class") out.section_id = entry.section_id;
    return out;
  }
  /** @param {any} view */
  const fromView = (view) => ({ first_day: view.first_day, last_day: view.last_day, entries: view.entries.map((x) => ({ ...x })), days_off: [...(view.days_off || [])] });

  function setHeader(group, termLabel) {
    const view = state?.view;
    const saved = view?.revision ? `Saved ${wallDate(view.updated_at.slice(0, 10))} · revision ${view.revision}` : "";
    const using = view?.in_use ? `On your Calendar and Google since ${wallDate(view.in_use_since.slice(0, 10))}`
      : "Your Calendar keeps its own events until you use this schedule.";
    header?.set({
      route: location.hash,
      title: "Term schedule",
      context: [group.code, termLabel].filter(Boolean).join(" · "),
      subtitle: [saved, using].filter(Boolean).join(" · "),
      crumbs: [
        { label: "Home", href: "#overview" },
        { label: "Courses", href: "#current-courses" },
        { label: [group.code, termLabel].filter(Boolean).join(" · "), href: `#course/${encodeURIComponent(group.id)}` },
        { label: "Term schedule" },
      ],
      // One primary by state: save an edit, import the first schedule, then use it (USE THIS SCHEDULE); none once in use.
      action: !view ? null : dirty() ? { label: "Save schedule", icon: "check", run: () => void save() }
        : view.revision === 0 && !state.draft.entries.length ? { label: "Import from your calendar", icon: "download", run: () => void importCalendar() }
          : view.revision > 0 && !view.in_use ? { label: "Use this schedule…", icon: "calendar", run: () => void openUse() } : null,
    });
  }

  /** Mount for a course group. @param {any} group @param {string} [termLabel] @param {boolean} [force] */
  async function render(group, termLabel = "", force = false) {
    const node = host();
    if (!node || !group) return;
    const same = state?.groupId === group.id;
    if (!same) state = { groupId: group.id, group, termLabel, view: null, draft: null, request: null, saving: false };
    state.group = group; state.termLabel = termLabel;
    setHeader(group, termLabel);
    if (same && state.view && !force && node.firstElementChild) return;
    const run = ++generation;
    if (!state.view) node.innerHTML = loadingMarkup("Opening the Term schedule…", 4);
    try {
      const view = await api(`/api/scheduling/groups/${encodeURIComponent(group.id)}`);
      if (run !== generation) return;
      const changed = dirty();
      state.view = view;
      if (!state.draft || !changed) state.draft = fromView(view);
      draw();
    } catch (cause) {
      if (run !== generation) return;
      node.innerHTML = emptyMarkup({ icon: "calendar", title: "The Term schedule could not be opened", body: errorMessage(cause), kind: "error" });
    }
  }

  function daysMarkup(entry, index) {
    return `<fieldset class="schedule-days"><legend class="sr-only">Days</legend>${DAYS.map((day, n) => `<label><input type="checkbox" data-day="${n}" data-row="${index}"${entry.weekdays.includes(n) ? " checked" : ""}><span>${day}</span></label>`).join("")}</fieldset>`;
  }

  /** @param {any} entry @param {number} index */
  function rowMarkup(entry, index) {
    const label = entry.kind === "class" ? "class meeting" : entry.kind === "office_hours" ? "office hours" : "meeting hours";
    const seats = entry.kind === "office_hours"
      ? `<label class="schedule-seats"><input type="checkbox" data-field="seats_makeup" data-row="${index}"${entry.seats_makeup ? " checked" : ""}> Seats a makeup</label>`
      : entry.kind === "meeting_hours" ? '<span class="schedule-seats field-hint">Hosts makeups</span>' : "";
    return `<tr data-row-index="${index}"><td>${daysMarkup(entry, index)}</td>
      <td><input type="time" step="300" aria-label="Starts" data-field="start" data-row="${index}" value="${e(entry.start)}"></td>
      <td><input type="time" step="300" aria-label="Ends" data-field="end" data-row="${index}" value="${e(entry.end)}"></td>
      <td><input aria-label="Room" maxlength="80" data-field="room" data-row="${index}" value="${e(entry.room)}" placeholder="BLOC 319"></td>
      <td>${seats}</td>
      <td><button type="button" class="text-button" data-remove="${index}" aria-label="Remove this ${label}">Remove</button></td></tr>`;
  }

  /** @param {Array<[any, number]>} rows */
  function tableMarkup(rows, caption) {
    if (!rows.length) return "";
    return `<table class="schedule-table"><caption class="sr-only">${e(caption)}</caption><thead><tr><th scope="col">Days</th><th scope="col">Starts</th><th scope="col">Ends</th><th scope="col">Room</th><th scope="col"><span class="sr-only">Makeups</span></th><th scope="col"><span class="sr-only">Remove</span></th></tr></thead><tbody>${rows.map(([entry, index]) => rowMarkup(entry, index)).join("")}</tbody></table>`;
  }

  function draw() {
    const node = host();
    if (!node || !state?.view) return;
    const { view, draft } = state;
    const indexed = draft.entries.map((entry, index) => /** @type {[any, number]} */ ([entry, index]));
    const sections = view.sections.map((section) => {
      const rows = indexed.filter(([entry]) => entry.kind === "class" && entry.section_id === section.id);
      return `<div class="schedule-section"><h4>Section ${e(section.number)}</h4>${tableMarkup(rows, `Section ${section.number} class meetings`) || '<p class="field-hint">No class meetings yet.</p>'}<button type="button" class="text-button" data-add="class" data-section="${e(section.id)}">${icon("plus")}Add a class meeting</button></div>`;
    }).join("");
    const hours = indexed.filter(([entry]) => entry.kind !== "class");
    const office = hours.filter(([entry]) => entry.kind === "office_hours"), meeting = hours.filter(([entry]) => entry.kind === "meeting_hours");
    node.innerHTML = `<form class="term-schedule" data-term-schedule novalidate>
      ${view.revision === 0 ? `<p class="schedule-lead">Set this term's weekly schedule once. ${draft.entries.length ? "Check what was read from your calendar, then save." : "Import it from the events already on your calendar, or add each time below."}</p>` : ""}
      <section class="schedule-region" aria-labelledby="schedule-dates"><h3 id="schedule-dates">Term dates</h3>
        <div class="schedule-dates"><label class="field">First day of classes<input type="date" data-term="first_day" value="${e(draft.first_day)}"></label>
        <label class="field">Last day of classes<input type="date" data-term="last_day" value="${e(draft.last_day)}"></label></div>
        <h4>Days off</h4><p class="field-hint">Dates without class or office hours (a holiday, a day run on another day's schedule).</p>
        <ul class="schedule-days-off">${[...(draft.days_off || [])].sort().map((day) => `<li><span>${e(wallDate(day))}</span><button type="button" class="text-button" data-off-remove="${e(day)}" aria-label="Remove ${e(wallDate(day))} from days off">Remove</button></li>`).join("") || '<li class="field-hint">None.</li>'}</ul>
        <div class="schedule-off-add"><label class="field">Add a day off<input type="date" data-off-new min="${e(draft.first_day)}" max="${e(draft.last_day)}"></label>
        <button type="button" class="button" data-off-add>Add</button>${view.revision && !view.in_use ? '<button type="button" class="text-button" data-off-find>Find days off from your calendar</button>' : ""}</div></section>
      <section class="schedule-region" aria-labelledby="schedule-classes"><h3 id="schedule-classes">Classes</h3>${sections}</section>
      <section class="schedule-region" aria-labelledby="schedule-hours"><h3 id="schedule-hours">Office and meeting hours</h3>
        <h4>Office hours</h4>${tableMarkup(office, "Office hours") || '<p class="field-hint">No office hours yet.</p>'}
        <button type="button" class="text-button" data-add="office_hours">${icon("plus")}Add office hours</button>
        <h4>Meeting hours</h4><p class="field-hint">When a student may meet you outside office hours; replies and makeup cards offer only open times inside them.</p>
        ${tableMarkup(meeting, "Meeting hours") || '<p class="field-hint">No meeting hours yet.</p>'}
        <button type="button" class="text-button" data-add="meeting_hours">${icon("plus")}Add meeting hours</button>
        <p class="schedule-canvas" data-schedule-canvas aria-live="polite"></p></section>
      <p class="form-error" role="alert" hidden></p>
      <footer class="schedule-footer"><p class="field-hint" data-schedule-status aria-live="polite"></p>
        ${view.revision === 0 && draft.entries.length ? `<button type="button" class="button" data-import>${icon("download")}Import from your calendar again</button>` : ""}
        ${view.in_use ? '<button type="button" class="text-button" data-stop-using>Stop using this schedule…</button>' : ""}</footer></form>`;
    progress();
    void canvasLine();
  }

  /** CANVAS OFFICE HOURS (stage 1f): whether the Office Hours page in Faculty's Canvas copy says what the saved schedule says. */
  async function canvasLine() {
    const slot = host()?.querySelector("[data-schedule-canvas]");
    if (!slot || !state?.view?.revision) return;
    const current = currentPage();
    try {
      const found = await api(`/api/canvas/content/${encodeURIComponent(state.view.term_id)}/office-hours`);
      if (!current() || !slot.isConnected) return;
      state.canvas = found;
      if (!found.available) { slot.textContent = ""; return; }
      slot.innerHTML = found.differs
        ? `The Office Hours page in your Canvas copy differs from this schedule. <button type="button" class="text-button" data-canvas-office>Update the copy…</button>`
        : "The Office Hours page in your Canvas copy matches this schedule.";
    } catch { slot.textContent = ""; }
  }

  function openCanvasOffice() {
    const found = state?.canvas;
    if (!found?.available) return;
    const list = (/** @type {string[]} */ items) => `<ul class="schedule-diff">${items.map((line) => `<li>${e(line)}</li>`).join("") || '<li class="field-hint">None</li>'}</ul>`;
    showDialog(`<form id="schedule-canvas">${dialogHeader("Update the Office Hours page", "In Faculty's copy of your Canvas pages")}<div class="dialog-body">
      <p>The page keeps its heading and pictures; its times become these, from this schedule.</p>
      <h3 class="schedule-diff-title">In person (regularly scheduled)</h3>${list(found.office)}
      <h3 class="schedule-diff-title">Or by appointment</h3>${list(found.meeting)}
      <p class="field-hint">Canvas changes when you push from <a href="#course/${encodeURIComponent(state.groupId)}/home/canvas">Canvas content</a>, as usual.</p>
      <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Put in Faculty's copy</button></footer></form>`);
    const form = /** @type {HTMLFormElement|null} */ (document.getElementById("schedule-canvas"));
    if (!form) return;
    const active = dialogSession(form, document.querySelector("#dialog")), current = currentPage(), termId = state.view.term_id;
    const request = crypto.randomUUID();
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        if (!active() || !current()) return;
        await api(`/api/canvas/content/${encodeURIComponent(termId)}/office-hours`, "POST", { request_id: request, base_revision: found.revision, confirmation: "put" });
        if (!active() || !current()) return;
        /** @type {HTMLDialogElement|null} */ (document.querySelector("#dialog"))?.close();
        toast("The Office Hours page in your Canvas copy now matches this schedule. Push it from Canvas content when you're ready.");
        void canvasLine();
      });
    };
  }

  function progress() {
    const node = host();
    if (!node || !state) return;
    const status = node.querySelector("[data-schedule-status]");
    if (status) status.textContent = state.saving ? "Saving…" : dirty() ? "Unsaved changes" : state.view.revision ? "Saved" : "Not saved yet";
    setHeader(state.group, state.termLabel);
  }

  /** @param {string} kind @param {string} [section] */
  function add(kind, section) {
    const last = [...state.draft.entries].reverse().find((entry) => entry.kind === kind && (kind !== "class" || entry.section_id === section));
    state.draft.entries.push(last ? { ...last, entry: undefined } : { kind, section_id: section || null, weekdays: kind === "class" ? [0, 2, 4] : [0],
      start: kind === "meeting_hours" ? "13:00" : kind === "office_hours" ? "15:00" : "08:00", end: kind === "class" ? "08:50" : "17:00",
      room: "", seats_makeup: kind === "meeting_hours" });
    draw();
  }

  async function importCalendar() {
    const node = host();
    const current = currentPage();
    try {
      const proposal = await api(`/api/scheduling/terms/${encodeURIComponent(state.view.term_id)}/import`);
      if (!current()) return;
      if (!proposal.entries.length) { toast("No weekly class or office-hours events were found on this term's calendar.", true); return; }
      state.draft = { first_day: proposal.first_day, last_day: proposal.last_day, entries: proposal.entries, days_off: proposal.days_off || [] };
      draw();
      toast(`Read ${proposal.entries.length} weekly times from ${proposal.from_events} calendar events. Check them, then save.`);
      node?.querySelector("[data-term=first_day]")?.focus();
    } catch (cause) { toast(errorMessage(cause), true); }
  }

  /** DAYS OFF the term's calendar keeps free, read for the saved schedule; added to the draft for the instructor to save. */
  async function findDaysOff() {
    const current = currentPage();
    try {
      const found = await api(`/api/scheduling/terms/${encodeURIComponent(state.view.term_id)}/days-off`);
      if (!current()) return;
      const added = found.days_off.filter((/** @type {string} */ d) => !(state.draft.days_off || []).includes(d));
      state.draft.days_off = [...new Set([...(state.draft.days_off || []), ...found.days_off])].sort();
      draw();
      toast(added.length ? `Added ${added.length} day${added.length === 1 ? "" : "s"} off your calendar keeps free. Check them, then save.` : "No other days off were found on your calendar.");
    } catch (cause) { toast(errorMessage(cause), true); }
  }

  async function save() {
    const node = host();
    if (!state || state.saving || !dirty()) return;
    const current = currentPage(), owner = state;
    const error = /** @type {HTMLElement|null} */ (node?.querySelector(".form-error"));
    const body = { first_day: state.draft.first_day, last_day: state.draft.last_day, entries: state.draft.entries.map(clean), days_off: [...(state.draft.days_off || [])].sort(), base_revision: state.view.revision };
    const content = JSON.stringify(body);
    // The same edit sent again (a retry) keeps its request ID; any change gets a new one.
    state.request = state.request?.content === content ? state.request : { content, id: crypto.randomUUID() };
    state.saving = true; progress();
    try {
      const view = await api(`/api/scheduling/terms/${encodeURIComponent(state.view.term_id)}`, "POST", { ...body, request_id: state.request.id });
      if (!current()) return;
      const sent = packed(state.draft) === JSON.stringify({ first_day: body.first_day, last_day: body.last_day, entries: body.entries, days_off: body.days_off });
      state.view = view;
      if (sent) state.draft = fromView(view); // FRONTEND: a reply to Save never replaces edits made while it was in flight
      if (error) error.hidden = true;
      toast(`Term schedule saved · revision ${view.revision}.`);
    } catch (cause) {
      if (current() && error) { error.textContent = errorMessage(cause); error.hidden = false; }
    } finally {
      owner.saving = false;
      if (current()) draw();
    }
  }

  /** A dated list of calendar rows for the Use window. @param {any[]} rows @param {number} count */
  function datedList(rows, count) {
    const more = count - rows.length;
    return `<ul class="schedule-diff">${rows.map((row) => `<li><span>${e(wallDate(row.date))}</span> ${e(row.time)}–${e(row.end_time)} · ${e(row.title)}${row.location ? ` · ${e(row.location)}` : ""}</li>`).join("")}${more > 0 ? `<li class="field-hint">and ${more} more</li>` : ""}</ul>`;
  }

  /** USE THIS SCHEDULE: the dry run (what is hidden, what stays, what is added), then Use against its fingerprint. */
  async function openUse() {
    if (!state?.view || dirty()) return;
    const current = currentPage(), termId = state.view.term_id;
    const intent = captureDialogIntent(document.querySelector("#dialog"));
    let plan;
    try { plan = await api(`/api/scheduling/terms/${encodeURIComponent(termId)}/use`); }
    catch (cause) { toast(errorMessage(cause), true); return; }
    if (!current() || !intent()) return;
    const kinds = Object.entries(plan.matched_by_kind || {}).map(([kind, n]) => `${n} ${kind === "class" ? "class meeting" : "office-hours"} event${n === 1 ? "" : "s"}`).join(" and ");
    const consequence = `Shows your schedule on the Calendar and Google, and hides ${plan.matched} calendar event${plan.matched === 1 ? "" : "s"} it replaces`;
    showDialog(`<form id="schedule-use">${dialogHeader("Use this schedule", "Check what changes before switching.")}<div class="dialog-body">
      ${calloutMarkup({ tone: "warning", title: consequence, body: `${kinds ? `${kinds} match this schedule one for one. ` : ""}Nothing is deleted: Stop using brings them back.` })}
      ${plan.added_count ? `<h3 class="schedule-diff-title">Added: ${plan.added_count} date${plan.added_count === 1 ? "" : "s"} without a calendar event today</h3>${datedList(plan.added, plan.added_count)}<p class="field-hint">A holiday or a day run on another day's schedule shows up here: cancel, add it to Days off (or use Find days off from your calendar), save, then use the schedule.</p>` : ""}
      ${plan.left_count ? `<h3 class="schedule-diff-title">Still showing: ${plan.left_count} calendar event${plan.left_count === 1 ? "" : "s"} the schedule does not match</h3>${datedList(plan.left, plan.left_count)}` : ""}
      ${!plan.added_count && !plan.left_count ? '<p>Every class and office-hours date matches your calendar exactly.</p>' : ""}
      <p class="field-hint">Google Calendar: the ${plan.replaced_from_today} events from today on are replaced there too over the next few minutes; edits you made to those events in Google are not kept.</p>
      <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Use this schedule</button></footer></form>`);
    const form = /** @type {HTMLFormElement|null} */ (document.getElementById("schedule-use"));
    if (!form) return;
    const active = dialogSession(form, document.querySelector("#dialog"));
    const request = crypto.randomUUID();
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        if (!active() || !current()) return;
        const done = await api(`/api/scheduling/terms/${encodeURIComponent(termId)}/use`, "POST", { request_id: request, fingerprint: plan.fingerprint, confirmation: "use" });
        if (!active() || !current()) return;
        /** @type {HTMLDialogElement|null} */ (document.querySelector("#dialog"))?.close();
        toast(`Your Calendar now shows this schedule; ${done.hidden} copied event${done.hidden === 1 ? "" : "s"} hidden.`);
        await render(state.group, state.termLabel, true);
      });
    };
  }

  function openStop() {
    if (!state?.view?.in_use) return;
    const current = currentPage(), termId = state.view.term_id;
    showDialog(`<form id="schedule-stop">${dialogHeader("Stop using this schedule", "")}<div class="dialog-body">
      <p>The Calendar and Google go back to the calendar events this schedule replaced. The schedule itself stays saved here.</p>
      <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Stop using</button></footer></form>`);
    const form = /** @type {HTMLFormElement|null} */ (document.getElementById("schedule-stop"));
    if (!form) return;
    const active = dialogSession(form, document.querySelector("#dialog"));
    const request = crypto.randomUUID();
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        if (!active() || !current()) return;
        const done = await api(`/api/scheduling/terms/${encodeURIComponent(termId)}/use/undo`, "POST", { request_id: request, confirmation: "undo" });
        if (!active() || !current()) return;
        /** @type {HTMLDialogElement|null} */ (document.querySelector("#dialog"))?.close();
        toast(`Back to your calendar events; ${done.restored} shown again.`);
        await render(state.group, state.termLabel, true);
      });
    };
  }

  document.addEventListener("input", (event) => {
    const target = /** @type {HTMLInputElement} */ (event.target);
    if (!state || !target.closest?.("[data-term-schedule]")) return;
    if (target.dataset.term) state.draft[target.dataset.term] = target.value;
    const row = target.dataset.row !== undefined ? state.draft.entries[Number(target.dataset.row)] : null;
    if (row && target.dataset.field) row[target.dataset.field] = target.type === "checkbox" ? target.checked : target.value;
    if (row && target.dataset.day !== undefined) {
      const day = Number(target.dataset.day);
      row.weekdays = target.checked ? [...new Set([...row.weekdays, day])].sort() : row.weekdays.filter((/** @type {number} */ d) => d !== day);
    }
    progress();
  });
  document.addEventListener("change", (event) => {
    const target = /** @type {HTMLElement} */ (event.target);
    if (state && target.closest?.("[data-term-schedule]") && target instanceof HTMLInputElement && target.type === "checkbox") progress();
  });
  document.addEventListener("click", (event) => {
    const button = /** @type {HTMLElement|null} */ (/** @type {HTMLElement} */ (event.target).closest?.("[data-term-schedule] button"));
    if (!button || !state) return;
    if (button.dataset.add) add(button.dataset.add, button.dataset.section);
    else if (button.dataset.remove !== undefined) { state.draft.entries.splice(Number(button.dataset.remove), 1); draw(); }
    else if (button.hasAttribute("data-import")) void importCalendar();
    else if (button.hasAttribute("data-stop-using")) openStop();
    else if (button.hasAttribute("data-canvas-office")) openCanvasOffice();
    else if (button.dataset.offRemove) { state.draft.days_off = state.draft.days_off.filter((/** @type {string} */ d) => d !== button.dataset.offRemove); draw(); }
    else if (button.hasAttribute("data-off-add")) {
      const value = /** @type {HTMLInputElement|null} */ (host()?.querySelector("[data-off-new]"))?.value;
      if (value) { state.draft.days_off = [...new Set([...(state.draft.days_off || []), value])].sort(); draw(); }
    } else if (button.hasAttribute("data-off-find")) void findDaysOff();
  });
  document.addEventListener("submit", (event) => {
    if (!(/** @type {HTMLElement} */ (event.target)).matches?.("[data-term-schedule]")) return;
    event.preventDefault();
    void save();
  });

  return { render };
}
