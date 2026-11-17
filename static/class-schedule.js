/**
 * A section's Class schedule (SCHEDULING "CLASS SCHEDULE"): `#course/<group>/section/<n>/schedule` renders into
 * [data-class-schedule-host]. One ruled table by date: the section's class days, what each covers, and the work given or due that
 * day. A date another record holds links to where it is changed; a planned row (work not created yet) and a day's topics are edited
 * here and saved to the term's plan, which every section of the term shares (a section's own date, time and place where it
 * differs). Orange marks only records that disagree about a date.
 */
import { calloutMarkup, disclosureMarkup, emptyMarkup, errorMessage, loadingMarkup, registerLeaveGuard, runFormAction, wallDate, dialogSession } from "./ui.js";
import { displayClock, displaySpan } from "./calendar-feed.js";
import { patchChildren } from "./dom-patch.js";
import { KINDS, formatTopics, packRow, parseTopics, scopeLine, splitWeeks, topicEntry, unpackRow } from "./class-schedule-model.js";

const LEAVE_QUESTION = "Leave the class schedule without saving your topics?";
const WHAT = /** @type {Record<string, string>} */ ({ given: "Given", due: "Due", date: "" });
const SOURCES = /** @type {Array<[string, string]>} */ ([["given", "Given"], ["gradebook", "Gradebook"], ["canvas", "Canvas copy"]]);
const differLabel = (/** @type {number} */ count) => (count === 1 ? "1 date differs" : `${count} dates differ`);
const COLUMNS = '<colgroup><col class="cs-col-week"><col class="cs-col-date"><col class="cs-col-class"><col class="cs-col-topics"><col class="cs-col-work"></colgroup>';
const HEAD = '<tr><th scope="col">Week</th><th scope="col">Date</th><th scope="col">Class</th><th scope="col">Topics</th><th scope="col">Given and due</th></tr>';

/** @param {{api: any, escape: (v: any) => string, toast: any, header?: any,
 *  showDialog?: (html: string) => void, dialogHeader?: (t: string, s?: string) => string}} deps */
export function createClassSchedule({ api, escape, toast, header = null, showDialog = () => {}, dialogHeader = (t) => t }) {
  const e = escape;
  /** @type {any} */ let state = null; // {key, group, termLabel, section, view, topics, saving, outline, request, stale}
  let left = false, shownAt = "";   // whether another page was shown since this one was read, and the address it was read at
  let generation = 0;
  /** @type {WeakMap<Element, string>} */ const written = new WeakMap();
  const host = () => /** @type {HTMLElement|null} */ (document.querySelector("[data-class-schedule-host]"));
  const dialog = () => /** @type {HTMLDialogElement|null} */ (document.querySelector("#dialog"));
  const sectionId = () => state?.view?.section_id;
  const topicsDirty = () => Boolean(state?.topics && Object.entries(state.topics.typed).some(([date, typed]) => {
    const first = state.topics.initial[date];
    return /** @type {any} */ (typed).text.trim() !== first.text || /** @type {any} */ (typed).only !== first.only;
  }));
  registerLeaveGuard(() => (topicsDirty() && host() ? LEAVE_QUESTION : null));
  const shownTime = (/** @type {string} */ text) => (/^\d\d:\d\d$/.test(text || "") ? displayClock(text) : text || "");
  const url = () => `/api/scheduling/groups/${encodeURIComponent(state.group.id)}/sections/${encodeURIComponent(state.section)}/class-schedule`;
  const planUrl = () => `/api/scheduling/terms/${encodeURIComponent(state.view.term_id)}/class-schedule`;
  const currentPage = () => {
    const owner = state, route = location.hash, node = host();
    return () => state === owner && location.hash === route && host() === node && Boolean(node?.isConnected);
  };

  function setHeader() {
    if (!state) return;
    const { group, termLabel, section, view } = state;
    const course = [group.code, termLabel].filter(Boolean).join(" · ");
    const classDays = view ? view.days.filter((/** @type {any} */ day) => day.meetings.length).length : 0;
    const term = view?.term_schedule;
    const differ = view?.differs.length || 0;
    const facts = !view ? "" : [differ ? differLabel(differ) : "", classDays ? `${classDays} class day${classDays === 1 ? "" : "s"}` : "",
      term?.first_day ? `${wallDate(term.first_day)} – ${wallDate(term.last_day)}` : ""].filter(Boolean).join(" · ");
    const add = { label: "Add a date", icon: "plus", run: () => openRow(null) };
    const empty = Boolean(view && !view.plan.items.length);
    header?.set({
      route: location.hash,
      title: `Class schedule · Section ${section}`,
      context: course,
      subtitle: facts,
      attention: differ > 0,
      crumbs: [
        { label: "Home", href: "#overview" },
        { label: "Courses", href: "#current-courses" },
        { label: course, href: `#course/${encodeURIComponent(group.id)}` },
        { label: `Section ${section}`, href: `#course/${encodeURIComponent(group.id)}/section/${section}` },
        { label: "Class schedule" },
      ],
      // One primary by state: save typed topics; add the first date while the plan is empty; otherwise none.
      action: !view ? null : state.topics ? (topicsDirty() ? { label: state.saving ? "Saving…" : "Save topics", icon: "check", run: () => void saveTopics() } : null)
        : empty ? add : null,
      secondary: !view ? [] : state.topics ? [{ label: topicsDirty() ? "Cancel" : "Done", run: () => leaveTopics() }]
        : [...(empty ? [] : [add]), { label: "Edit topics", icon: "edit", run: () => void enterTopics() }],
    });
  }

  /** Mount for a section of a course group. @param {any} group @param {string} termLabel @param {number} section @param {boolean} [force] */
  async function render(group, termLabel, section, force = false) {
    const node = host();
    if (!node || !group || section === null || section === undefined) return;
    const key = `${group.id}:${section}`;
    const same = state?.key === key;
    if (!same) state = { key, group, termLabel, section, view: null, topics: null, saving: false, outline: null, request: null, stale: false };
    state.group = group; state.termLabel = termLabel;
    // Coming back to the page after leaving it (the leave guard asked first): typed topics were given up, so it opens to read.
    if (same && !node.firstElementChild && !state.saving) { state.topics = null; state.conflict = false; }
    setHeader();
    // A shell redraw of the same address keeps the page; so does anything being typed or saved. Coming BACK to it from
    // another page reads it again, quietly and in place: the plan may have changed meanwhile (a move approved on a class
    // recording's page, a card approved in the Assistant).
    const back = left;
    shownAt = location.hash;
    if (same && state.view && node.firstElementChild && !force && !back) return;
    if (same && (state.topics || state.saving) && node.firstElementChild) return;
    left = false;
    if (state.view) draw(); else node.innerHTML = loadingMarkup("Opening the class schedule…", 6);
    const run = ++generation;
    try {
      const view = await api(url());
      if (run !== generation || state?.key !== key) return;
      state.view = view; state.stale = false;
      draw();
    } catch (cause) {
      if (run !== generation || state?.key !== key) return;
      if (state.view) { state.stale = true; draw(); return; }
      node.innerHTML = emptyMarkup({ icon: "calendar", title: "The class schedule could not be opened", body: errorMessage(cause), kind: "first-use" });
      written.delete(node);
    }
  }

  // -- The table ----------------------------------------------------------------------------------------------------------------
  /** @param {any} day */
  function classCell(day) {
    if (day.off) return '<span class="cs-quiet">No class</span>';
    return day.meetings.map((/** @type {any} */ meeting) => `<span class="cs-quiet">${e(displaySpan(meeting.time, meeting.end_time))}${meeting.room ? ` · ${e(meeting.room)}` : ""}</span>`).join("<br>");
  }

  /** @param {any} day */
  function topicsCell(day) {
    if (state.topics) {
      const typed = state.topics.typed[day.date];
      if (!typed) return "";
      const label = `Topics for ${wallDate(day.date)}`;
      return `<div class="cs-topic-edit"><input class="cs-topic-input" data-topic="${e(day.date)}" value="${e(typed.text)}" aria-label="${e(label)}" maxlength="300" placeholder="2.3 2.4" autocomplete="off">
        <label class="cs-topic-only"><input type="checkbox" data-topic-only="${e(day.date)}"${typed.only ? " checked" : ""}>Only ${e(state.section)}</label></div>
        <small class="field-hint" data-topic-preview="${e(day.date)}">${e(preview(typed.text))}</small>`;
    }
    const topics = day.topics;
    if (!topics) return recordedLine(day);
    const notes = topics.notes.map((/** @type {any} */ note) => {
      const text = `${note.number}${note.title ? ` ${note.title}` : ""}`;
      return note.url ? `<a href="${e(note.url)}" target="_blank" rel="noopener">${e(text)}</a>` : `<span>${e(text)}</span>`;
    });
    return `${[...notes, topics.note ? `<span>${e(topics.note)}</span>` : ""].filter(Boolean).join('<span class="cs-sep"> · </span>')}${topics.own ? ' <span class="chip" data-tone="neutral">This section</span>' : ""}${recordedLine(day)}`;
  }

  /** What a recording of the class day reached (RECORDINGS "Classes"), by reference: a link to the recording, and in orange a
   * planned section that was not reached (the move is offered on the recording's page). @param {any} day */
  function recordedLine(day) {
    const recorded = day.recorded;
    if (!recorded) return "";
    const reached = recorded.reached.map((/** @type {any} */ note) => note.number).join(", ");
    const text = !recorded.read ? "Recorded, not read yet" : reached ? `Reached ${reached}` : "Recorded: no notes section heard";
    const missing = !recorded.not_reached.length ? "" : recorded.differs ? ` <span class="chip" data-tone="warning">Not reached ${e(recorded.not_reached.join(", "))}</span>`
      : ` <span class="cs-quiet">not reached ${e(recorded.not_reached.join(", "))}, plan left as it is</span>`;
    return `<div class="cs-recorded" data-recorded-day="${e(day.date)}"><a href="${e(recorded.href)}">${e(text)}</a>${missing}</div>`;
  }

  /** What the notes outline calls the numbers typed, for the line under a topics field. @param {string} text */
  function preview(text) {
    const typed = parseTopics(text);
    const outline = state.outline || {};
    return typed.notes.map((number) => (outline[number] ? `${number} ${outline[number]}` : number)).join(" · ");
  }

  /** The other dates of a disagreement, each a link to where it is changed. @param {any} row */
  function differLinks(row) {
    const found = state.view.differs.filter((/** @type {any} */ entry) => entry.key === row.key);
    /** @type {Map<string, string>} */ const links = new Map();
    for (const entry of found) {
      for (const [source, label] of SOURCES) {
        const date = source === "given" ? entry.dates.given_record || entry.dates.given : entry.dates[source];
        if (!date) continue;
        const text = `${label} ${wallDate(date)}`;
        links.set(source, entry.hrefs[source] ? `<a href="${e(entry.hrefs[source])}">${e(text)}</a>` : `<span>${e(text)}</span>`);
      }
    }
    return [...links.values()].join('<span class="cs-sep"> · </span>');
  }

  /** One line of work. @param {any} row @param {boolean} dated */
  function workLine(row, dated = true) {
    const match = state.view.plan.open.find((/** @type {any} */ open) => open.item === row.item)?.match;
    const editable = row.item && (row.planned || !dated);
    const title = editable ? `<button type="button" class="text-button cs-title" data-edit-row="${e(row.item)}">${e(row.title)}</button>`
      : row.href ? `<a class="cs-title" href="${e(row.href)}">${e(row.title)}</a>` : `<span class="cs-title">${e(row.title)}</span>`;
    const facts = [shownTime(row.time), row.place].filter(Boolean).map((fact) => e(fact)).join(" · ");
    const marks = [
      editable ? '<span class="chip" data-tone="neutral">Planned</span>' : "",
      row.archived ? '<span class="chip" data-tone="neutral">Certified</span>' : "",
      row.differs.length ? `<span class="chip" data-tone="warning">Dates differ</span><span class="cs-differ">${differLinks(row)}</span>` : "",
      row.planned_was ? `<span class="cs-quiet">planned ${e(wallDate(row.planned_was))}</span>` : "",
      row.item && !editable ? `<button type="button" class="text-button" data-edit-row="${e(row.item)}" aria-label="The planned row ${e(row.title)} follows">Plan</button>` : "",
      match && !row.linked ? `<button type="button" class="text-button" data-link-row="${e(row.item)}">Link to ${e(match.title)}</button>` : "",
    ].filter(Boolean).join(" ");
    const what = WHAT[row.what] || "";
    // The label keeps its own column, so a long title wraps under itself and the labels of a day line up.
    return `<li data-work="${e(row.key)}"><span class="cs-what">${what}</span><span class="cs-line">${title}${facts ? ` <span class="cs-facts">${facts}</span>` : ""}${marks ? ` ${marks}` : ""}</span></li>`;
  }

  /** @param {any} day @param {boolean} first whether it opens its week @param {number} week */
  function dayRow(day, first, week) {
    const today = day.date === state.view.today;
    const classes = ["cs-day", day.date < state.view.today ? "cs-past" : "", day.work.some((/** @type {any} */ row) => row.differs.length) || day.recorded?.differs ? "cs-differs" : ""].filter(Boolean).join(" ");
    return `<tr class="${classes}" data-patch-key="${e(day.date)}" data-date="${e(day.date)}" tabindex="-1"${today ? ' aria-current="date"' : ""}>
      <td class="cs-week">${first && week ? week : ""}</td>
      <th scope="row" class="cs-date">${e(wallDate(day.date))}</th>
      <td>${classCell(day)}</td>
      <td class="cs-topics">${topicsCell(day)}</td>
      <td>${day.work.length ? `<ul class="cs-work">${day.work.map((/** @type {any} */ row) => workLine(row)).join("")}</ul>` : ""}</td></tr>`;
  }

  /** @param {any[]} weeks @param {{head?: boolean, after?: any[], label?: string}} [options] */
  function tableMarkup(weeks, { head = true, after = [], label = "Class schedule" } = {}) {
    const bodies = weeks.map((week) => `<tbody data-patch-key="${e(week.monday)}">${week.days.map((/** @type {any} */ day, /** @type {number} */ n) => dayRow(day, n === 0, week.number)).join("")}</tbody>`).join("");
    const later = after.length ? `<tbody data-patch-key="after"><tr class="cs-divider"><th colspan="5" scope="rowgroup">After classes end</th></tr>${after.flatMap((week) => week.days).map((/** @type {any} */ day) => dayRow(day, false, 0)).join("")}</tbody>` : "";
    return `<div class="workspace-table-wrap"><table class="workspace-table class-schedule-table"><caption class="sr-only">${e(label)}</caption>${COLUMNS}<thead${head ? "" : ' class="sr-only"'}>${HEAD}</thead>${bodies}${later}</table></div>`;
  }

  function draw() {
    const node = host();
    if (!node || !state?.view) return;
    const view = state.view, term = view.term_schedule;
    const runs = splitWeeks(view.days, view.today, term.first_day, term.last_day);
    const hasClass = view.days.some((/** @type {any} */ day) => day.meetings.length);
    const jump = view.differs.slice(0, 3).map((/** @type {any} */ entry) => `<button type="button" class="text-button" data-jump="${e(entry.date)}">${e(entry.title)}</button>`).join(" ");
    const more = view.differs.length - Math.min(3, view.differs.length);
    const earlierDiffer = runs.earlier.flatMap((week) => week.days).filter((/** @type {any} */ day) => day.work.some((/** @type {any} */ row) => row.differs.length) || day.recorded?.differs).length;
    const weeks = runs.earlier.length;
    const earlier = weeks ? disclosureMarkup({
      label: "Earlier", hint: `${weeks} week${weeks === 1 ? "" : "s"}${earlierDiffer ? ` · ${earlierDiffer} differ` : ""}`,
      // A fold holding a disagreement starts open, whatever was remembered: nothing that needs you is hidden.
      key: earlierDiffer ? "" : "class-schedule:earlier", open: earlierDiffer > 0, className: "cs-earlier",
      body: tableMarkup(runs.earlier, { head: false, label: "Earlier weeks" }) }) : "";
    const undated = view.undated.length ? `<section class="cs-undated" aria-labelledby="cs-undated-title"><h3 id="cs-undated-title">No date yet</h3><ul class="cs-work">${view.undated.map((/** @type {any} */ row) => workLine(row, false)).join("")}</ul></section>` : "";
    const markup = `<div class="class-schedule" data-class-schedule>
      ${state.stale ? calloutMarkup({ tone: "warning", title: "Couldn't refresh. Showing the last loaded schedule." }) : ""}
      ${state.conflict ? `<div class="callout" data-tone="warning" role="status"><strong class="callout-title">This schedule changed elsewhere.</strong><p>Your typed topics are kept.</p><button type="button" class="button callout-action" data-reload-topics>Reload the schedule</button></div>` : ""}
      ${hasClass ? "" : calloutMarkup({ tone: "info", title: "No class days yet", body: term.revision ? "The Term schedule is saved but not in use." : "Set the Term schedule first.",
        action: { label: "Open the Term schedule", href: `#course/${encodeURIComponent(state.group.id)}/home/schedule` } })}
      ${view.differs.length ? `<p class="cs-attention"><span class="chip" data-tone="warning">${differLabel(view.differs.length)}</span> ${jump}${more > 0 ? ` <span class="cs-quiet">${more} more</span>` : ""}</p>` : ""}
      ${view.unclear.length ? `<p class="field-hint">Canvas copy unclear: ${e(view.unclear.map((/** @type {any} */ entry) => entry.title).join(", "))}. <a href="#course/${encodeURIComponent(state.group.id)}/home/canvas">Open Canvas content</a></p>` : ""}
      ${earlier}
      ${runs.current.length || runs.after.length ? tableMarkup(runs.current, { after: runs.after }) : (view.days.length ? "" : emptyMarkup({ icon: "calendar", title: "Nothing on this schedule yet", body: "Dates Faculty holds appear here; add the ones planned." }))}
      ${undated}
      <p class="form-error" role="alert" data-schedule-error hidden></p></div>`;
    if (written.get(node) !== markup) {
      if (node.querySelector("[data-class-schedule]")) patchChildren(node, markup); else node.innerHTML = markup;
      written.set(node, markup);
    }
    setHeader();
  }

  // -- Topics ---------------------------------------------------------------------------------------------------------------------
  async function enterTopics() {
    if (!state?.view || state.topics) return;
    const current = currentPage();
    if (state.outline === null) {
      try {
        const notes = await api(`/api/course-notes/definitions/${encodeURIComponent(state.view.definition_id)}`);
        if (!current()) return;
        state.outline = Object.fromEntries((notes.current?.parts || []).filter((/** @type {any} */ part) => part.kind === "section").map((/** @type {any} */ part) => [part.number, part.title]));
      } catch { if (!current()) return; state.outline = {}; }
    }
    /** @type {Record<string, {text: string, only: boolean}>} */ const typed = {};
    for (const day of state.view.days) if (day.meetings.length || day.topics) typed[day.date] = { text: formatTopics(day.topics), only: Boolean(day.topics?.own) };
    state.topics = { typed, initial: JSON.parse(JSON.stringify(typed)) };
    draw();
    const first = host()?.querySelector(`[data-topic="${CSS.escape(state.view.today)}"]`) || host()?.querySelector(".cs-day:not(.cs-past) [data-topic]") || host()?.querySelector("[data-topic]");
    /** @type {HTMLElement|null} */ (first)?.focus();
  }

  function leaveTopics() {
    if (!state?.topics || state.saving) return;
    if (topicsDirty() && !window.confirm(LEAVE_QUESTION)) return;
    state.topics = null; state.conflict = false;
    draw();
  }

  async function saveTopics() {
    if (!state?.topics || state.saving || !topicsDirty()) return;
    const current = currentPage(), owner = state;
    const sentTopics = structuredClone(state.topics.typed);
    /** @type {Record<string, any>} */ const topics = {};
    for (const [date, typed] of Object.entries(state.topics.typed)) {
      const first = state.topics.initial[date];
      if (typed.text.trim() === first.text && typed.only === first.only) continue;
      topics[date] = topicEntry(state.view.plan.topics[date], sectionId(), parseTopics(typed.text), typed.only);
    }
    const body = { base_revision: state.view.revision, topics };
    const content = JSON.stringify(body);
    // The same save sent again (a retry) keeps its request ID; any change gets a new one.
    state.request = state.request?.content === content ? state.request : { content, id: crypto.randomUUID() };
    const error = /** @type {HTMLElement|null} */ (host()?.querySelector("[data-schedule-error]"));
    state.saving = true; setHeader();
    try {
      await api(planUrl(), "POST", { ...body, request_id: state.request.id });
      if (!current()) return;
      const latest = await api(url());
      if (!current()) return;
      state.view = latest;
      state.topics.initial = sentTopics;
      if (JSON.stringify(state.topics.typed) === JSON.stringify(sentTopics)) state.topics = null;
      state.conflict = false; state.saving = false;
      toast("Topics saved.");
      draw();
    } catch (cause) {
      owner.saving = false;
      if (!current()) return;
      if (/** @type {any} */ (cause)?.status === 409) { state.conflict = true; written.delete(/** @type {Element} */ (host())); redrawTopics(); }
      else if (error) { error.textContent = errorMessage(cause); error.hidden = false; }
      setHeader();
    } finally {
      owner.saving = false;
    }
  }

  /** Topics mode redrawn around what is typed (its inputs carry it in their markup). */
  function redrawTopics() {
    const node = host();
    if (!node) return;
    written.delete(node);
    node.innerHTML = "";
    draw();
  }

  async function reloadForTopics() {
    const current = currentPage();
    try {
      const view = await api(url());
      if (!current()) return;
      state.view = view;
      state.conflict = false;
      redrawTopics();
      toast(`Loaded revision ${state.view.revision}. Check and save again.`);
    } catch (cause) { toast(errorMessage(cause), true); }
  }

  // -- A planned row ---------------------------------------------------------------------------------------------------------------
  /** @param {string|null} item */
  function openRow(item) {
    if (!state?.view || state.topics) return;
    const view = state.view, sections = view.sections;
    const stored = item ? view.plan.items.find((/** @type {any} */ row) => row.item === item) : null;
    if (item && !stored) return;
    const row = stored || { kind: "quiz", title: "", sections: [], date: "", time: "", place: "", due_date: "", section_values: {}, note: "" };
    const shown = unpackRow({ ...row, sections: [] }, sections);
    const all = !row.sections.length;
    const open = view.plan.open.find((/** @type {any} */ entry) => entry.item === item);
    const follows = stored?.link ? view.days.flatMap((/** @type {any} */ day) => day.work).concat(view.undated).find((/** @type {any} */ work) => work.item === item) : null;
    const linkLine = stored?.link
      ? `<p class="field-hint">Follows ${follows ? `<strong>${e(follows.title)}</strong>` : "a record"}: its own dates show on the schedule. <button type="button" class="text-button" data-unlink>Unlink</button></p>`
      : open?.match ? `<p class="field-hint"><button type="button" class="text-button" data-link>Link to ${e(open.match.title)}</button> so this row follows its dates.</p>` : "";
    const more = `<label class="field">Due on another day <span class="field-optional">optional</span><input type="date" name="due_date" value="${e(row.due_date)}"></label>
      <label class="field">Note <span class="field-optional">optional</span><input name="note" maxlength="200" value="${e(row.note)}"></label>`;
    showDialog(`<form id="class-schedule-row" novalidate>${dialogHeader(stored ? "Planned date" : "Add a date", e(`Section ${state.section} · ${[state.group.code, state.termLabel].filter(Boolean).join(" · ")}`))}<div class="dialog-body cs-form">
      <div class="cs-form-top"><label class="field">Title<input name="title" required maxlength="120" value="${e(row.title)}" placeholder="Quiz 6"></label>
      <label class="field">Kind<select name="kind">${KINDS.map(([value, label]) => `<option value="${value}"${value === row.kind ? " selected" : ""}>${label}</option>`).join("")}</select></label></div>
      <fieldset class="cs-form-sections"><legend>For</legend>
        <label class="checkbox-field"><input type="checkbox" name="all_sections"${all ? " checked" : ""}>All sections</label>
        ${sections.map((/** @type {any} */ section) => `<label class="checkbox-field"><input type="checkbox" name="section" value="${e(section.id)}"${row.sections.includes(section.id) ? " checked" : ""}${all ? " disabled" : ""}>Section ${e(section.number)}</label>`).join("")}</fieldset>
      <div class="cs-form-shared" data-shared${shown.differ ? " hidden" : ""}>
        <label class="field">Date<input type="date" name="date" value="${e(row.date)}"></label>
        <label class="field">Time<input name="time" maxlength="40" value="${e(row.time)}" placeholder="9:00 AM–10:15 AM"></label>
        <label class="field">Place<input name="place" maxlength="80" value="${e(row.place)}" placeholder="HELD 107"></label></div>
      <label class="checkbox-field"><input type="checkbox" name="differ"${shown.differ ? " checked" : ""}>Sections differ</label>
      <table class="cs-form-table" data-per-section${shown.differ ? "" : " hidden"}><caption class="sr-only">Each section's date, time and place</caption>
        <thead><tr><th scope="col">Section</th><th scope="col">Date</th><th scope="col">Time</th><th scope="col">Place</th></tr></thead>
        <tbody>${shown.rows.map((/** @type {any} */ entry) => `<tr data-section-row="${e(entry.id)}"><th scope="row">${e(entry.number)}</th>
          <td><input type="date" data-own="date" value="${e(entry.date)}" aria-label="Section ${e(entry.number)} date"></td>
          <td><input data-own="time" maxlength="40" value="${e(entry.time)}" aria-label="Section ${e(entry.number)} time"></td>
          <td><input data-own="place" maxlength="80" value="${e(entry.place)}" aria-label="Section ${e(entry.number)} place"></td></tr>`).join("")}</tbody></table>
      ${disclosureMarkup({ label: "More", body: more, open: Boolean(row.due_date || row.note), className: "cs-form-more" })}
      ${linkLine}
      <p class="cs-scope" data-scope aria-live="polite"></p>
      <div class="callout" data-tone="warning" data-conflict hidden></div>
      <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer">${stored ? '<button type="button" class="text-button cs-remove" data-remove>Remove…</button>' : ""}<button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${stored ? "Save" : "Add to schedule"}</button></footer></form>`);
    const form = /** @type {HTMLFormElement|null} */ (document.getElementById("class-schedule-row"));
    if (!form) return;
    const current = currentPage(), active = dialogSession(form, dialog());
    const endpoint = planUrl(), readUrl = url();
    const field = (/** @type {string} */ name) => /** @type {HTMLInputElement} */ (form.elements.namedItem(name));
    const boxes = () => /** @type {HTMLInputElement[]} */ ([...form.querySelectorAll('input[name="section"]')]);
    const chosen = () => (field("all_sections").checked ? sections : sections.filter((/** @type {any} */ section) => boxes().find((box) => box.value === section.id)?.checked));
    /** The form's rows: each chosen section's date, time and place. */
    const rows = () => chosen().map((/** @type {any} */ section) => {
      const own = form.querySelector(`[data-section-row="${CSS.escape(section.id)}"]`);
      const value = (/** @type {string} */ name) => (field("differ").checked
        ? /** @type {HTMLInputElement} */ (own?.querySelector(`[data-own="${name}"]`)).value : field(name).value).trim();
      return { id: section.id, number: section.number, date: value("date"), time: value("time"), place: value("place") };
    });
    const refresh = () => {
      const differ = field("differ").checked, everyone = field("all_sections").checked;
      for (const box of boxes()) box.disabled = everyone;
      const named = new Set(chosen().map((/** @type {any} */ section) => section.id));
      for (const line of form.querySelectorAll("[data-section-row]")) /** @type {HTMLElement} */ (line).hidden = !named.has(/** @type {HTMLElement} */ (line).dataset.sectionRow);
      /** @type {HTMLElement} */ (form.querySelector("[data-shared]")).hidden = differ;
      /** @type {HTMLElement} */ (form.querySelector("[data-per-section]")).hidden = !differ;
      /** @type {HTMLElement} */ (form.querySelector("[data-scope]")).textContent = scopeLine(rows(), sections.length, wallDate);
      /** @type {HTMLInputElement} */ (form.elements.namedItem("due_date")).closest("label").hidden = !["quiz", "exam", "final"].includes(field("kind").value);
    };
    field("differ").addEventListener("change", () => {
      if (field("differ").checked) {
        // Each section starts from the shared values, ready for the one that differs.
        for (const line of form.querySelectorAll("[data-section-row]")) for (const name of ["date", "time", "place"]) {
          const own = /** @type {HTMLInputElement} */ (line.querySelector(`[data-own="${name}"]`));
          if (!own.value) own.value = field(name).value;
        }
      } else {
        const packed = packRow(chosen().map((/** @type {any} */ section) => {
          const line = form.querySelector(`[data-section-row="${CSS.escape(section.id)}"]`);
          const own = (/** @type {string} */ name) => /** @type {HTMLInputElement} */ (line?.querySelector(`[data-own="${name}"]`)).value.trim();
          return { id: section.id, date: own("date"), time: own("time"), place: own("place") };
        }));
        for (const name of ["date", "time", "place"]) field(name).value = /** @type {any} */ (packed)[name];
      }
      refresh();
    });
    form.addEventListener("input", refresh);
    form.addEventListener("change", refresh);
    refresh();
    if (!stored) field("title").focus();

    let request = /** @type {{content: string, id: string}|null} */ (null);
    /** @param {any} body @param {string} done */
    const send = (body, done, path = endpoint) => runFormAction(form, async () => {
      if (!current() || !active()) return;
      const content = JSON.stringify(body);
      request = request?.content === content ? request : { content, id: crypto.randomUUID() };
      try {
        await api(path, "POST", { ...body, request_id: request.id });
      } catch (cause) {
        if (!current() || !active()) return;
        if (/** @type {any} */ (cause)?.status !== 409 || !("base_revision" in body)) throw cause;
        const conflict = /** @type {HTMLElement} */ (form.querySelector("[data-conflict]"));
        conflict.innerHTML = '<strong class="callout-title">This schedule changed elsewhere.</strong><p>Your entries here are kept.</p><button type="button" class="button callout-action" data-reload>Reload the schedule</button>';
        conflict.hidden = false;
        return;
      }
      if (!current() || !active()) return;
      dialog()?.close();
      toast(done);
      await render(state.group, state.termLabel, state.section, true);
    });
    form.addEventListener("click", (event) => {
      const button = /** @type {HTMLElement|null} */ (/** @type {HTMLElement} */ (event.target).closest("button"));
      if (!button) return;
      if (button.hasAttribute("data-reload")) {
        void (async () => {
          try {
            const latest = await api(readUrl);
            if (!current() || !active()) return;
            state.view = latest;
            const conflict = /** @type {HTMLElement} */ (form.querySelector("[data-conflict]"));
            const still = !item || state.view.plan.items.some((/** @type {any} */ entry) => entry.item === item);
            conflict.innerHTML = still ? `<p>Loaded revision ${e(state.view.revision)}. Check and save again.</p>` : "<p>This row was removed in the meantime, so it cannot be saved.</p>";
            /** @type {HTMLButtonElement} */ (form.querySelector('[type="submit"]')).disabled = !still;
          } catch (cause) { toast(errorMessage(cause), true); }
        })();
      } else if (button.hasAttribute("data-remove")) {
        // Confirmed in place: the first click asks, the second removes.
        if (button.dataset.armed) void send({ base_revision: state.view.revision, remove: [item] }, "Removed from the schedule.");
        else { button.dataset.armed = "1"; button.textContent = "Remove from the schedule"; }
      } else if (button.hasAttribute("data-unlink")) {
        void send({ item, action: "unlink" }, "Unlinked. The row shows its planned date again.", `${endpoint}/links`);
      } else if (button.hasAttribute("data-link") && open?.match) {
        void send({ item, ...targetOf(open.match) }, `Linked to ${open.match.title}.`, `${endpoint}/links`);
      }
    });
    form.onsubmit = (event) => {
      event.preventDefault();
      const title = field("title").value.trim();
      const error = /** @type {HTMLElement} */ (form.querySelector(".form-error"));
      if (!title || !chosen().length) { error.textContent = title ? "Choose at least one section." : "Give it a title."; error.hidden = false; return; }
      const everyone = field("all_sections").checked;
      const packed = field("differ").checked ? packRow(rows()) : { date: field("date").value, time: field("time").value.trim(), place: field("place").value.trim(), section_values: {} };
      const given = ["quiz", "exam", "final"].includes(field("kind").value);
      const saved = { ...(item ? { item } : {}), kind: field("kind").value, title, sections: everyone ? [] : chosen().map((/** @type {any} */ section) => section.id),
        ...packed, due_date: given ? field("due_date").value : "", note: field("note").value.trim() };
      void send({ base_revision: state.view.revision, set: [saved] }, stored ? "Planned date saved." : "Added to the schedule.");
    };
  }

  /** @param {any} match */
  const targetOf = (match) => (match.assignment_id ? { assignment_id: match.assignment_id } : { gradebook_item: match.gradebook_item });

  /** A planned row linked to the record of its title, from its line on the page. @param {string} item */
  async function linkRow(item) {
    const current = currentPage();
    const open = state.view.plan.open.find((/** @type {any} */ entry) => entry.item === item);
    if (!open?.match) return;
    try {
      await api(`${planUrl()}/links`, "POST", { request_id: crypto.randomUUID(), item, ...targetOf(open.match) });
      if (!current()) return;
      toast(`Linked to ${open.match.title}.`);
      await render(state.group, state.termLabel, state.section, true);
    } catch (cause) { toast(errorMessage(cause), true); }
  }

  /** @param {string} date */
  function jumpTo(date) {
    const row = /** @type {HTMLElement|null} */ (host()?.querySelector(`tr[data-date="${CSS.escape(date)}"]`));
    if (!row) return;
    const fold = row.closest("details");
    if (fold) fold.open = true;
    row.scrollIntoView({ block: "center" });
    row.focus({ preventScroll: true });
  }

  document.addEventListener("click", (event) => {
    const target = /** @type {HTMLElement} */ (event.target);
    const button = /** @type {HTMLElement|null} */ (target.closest?.("[data-class-schedule] button"));
    if (!button || !state?.view) return;
    if (button.dataset.editRow) openRow(button.dataset.editRow);
    else if (button.dataset.linkRow) void linkRow(button.dataset.linkRow);
    else if (button.dataset.jump) jumpTo(button.dataset.jump);
    else if (button.hasAttribute("data-reload-topics")) void reloadForTopics();
  });
  document.addEventListener("input", (event) => {
    const target = /** @type {HTMLInputElement} */ (event.target);
    if (!state?.topics || !target.closest?.("[data-class-schedule]")) return;
    const date = target.dataset.topic || target.dataset.topicOnly;
    if (!date || !state.topics.typed[date]) return;
    if (target.dataset.topic) {
      state.topics.typed[date].text = target.value;
      const line = host()?.querySelector(`[data-topic-preview="${CSS.escape(date)}"]`);
      if (line) line.textContent = preview(target.value);
    } else state.topics.typed[date].only = target.checked;
    setHeader();
  });
  document.addEventListener("keydown", (event) => {
    const target = /** @type {HTMLInputElement} */ (event.target);
    if (!state?.topics || !target.dataset?.topic || !["Enter", "ArrowDown", "ArrowUp"].includes(event.key)) return;
    const inputs = /** @type {HTMLInputElement[]} */ ([...(host()?.querySelectorAll("[data-topic]") || [])].filter((input) => !input.closest("details:not([open])")));
    const next = inputs[inputs.indexOf(target) + (event.key === "ArrowUp" ? -1 : 1)];
    event.preventDefault();
    next?.focus();
    next?.select();
  });

  // Another page of the app was shown since this one was read: the next visit reads it again.
  window.addEventListener("hashchange", () => { if (state?.view && location.hash !== shownAt) left = true; });

  return { render };
}
