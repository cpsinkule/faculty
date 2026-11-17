import { runFormAction, dialogSession, registerShortcut, wallDate, daysFrom, loadingMarkup } from "./ui.js";
import { patchChildren } from "./dom-patch.js";
import { TYPE_LABELS, TYPE_FILTERS, eventType, clockOrder, displayTime, groupEvents, groupFacts, chipLabel, chipFacts, nextEvents, campusToday, addDays } from "./calendar-feed.js";
import { openGoogleCalendar, syncBadge } from "./calendar-google.js";

// Calendar dates are wall-clock values. UTC is only used for date arithmetic;
// no exam or DR time is converted through the browser's timezone.
const pad = value => String(value).padStart(2, "0");
const iso = date => `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
const dayDate = value => new Date(`${value}T12:00:00Z`);
const formatDay = (value, options = {weekday: "long", month: "long", day: "numeric"}) => new Intl.DateTimeFormat(undefined, {...options, timeZone: "UTC"}).format(dayDate(value));
const overlaps = (event, start, end = start) => event.date <= end && (event.end_date || event.date) >= start;
const NEXT_DAYS = 60; // "Next up" looks this far ahead of campus today.
const NEXT_EVERY = 60000; // ... and is read again at most once a minute.

export function createCalendar({api, escape, icon, toast, showDialog, dialogHeader, header = null}) {
  const root = () => document.getElementById("calendar-section");
  const e = value => escape(String(value ?? ""));
  let today = campusToday(), month = today.slice(0, 7), selected = today, mode = "month", span = "day"; // the agenda opens on one day; Week and Month widen it
  let course = "", type = "", data = null, signature = "", route = "", generation = 0, pending = null;
  let loadedRange = "", loadError = "", dialogIntent = 0, addRequested = false, googleRequested = false;
  // Next up: the coming groups from campus today on, read separately from the month on screen.
  let upcoming = null, upcomingAt = 0, upcomingDay = "", upcomingGeneration = 0, upcomingPending = null;
  const active = () => location.hash.split("?")[0] === "#calendar";
  const option = (value, label, current = "") => `<option value="${e(value)}" ${String(value) === String(current) ? "selected" : ""}>${e(label)}</option>`;
  const errorLine = () => '<p class="form-error" role="alert" hidden></p>';
  const firstDay = () => month + "-01";
  function lastDay() { const date = dayDate(firstDay()); date.setUTCMonth(date.getUTCMonth() + 1, 0); return iso(date); }
  // The days the agenda lists: the selected day, its week (Sunday to Saturday) or the month.
  function agendaRange() {
    if (span === "day") return {start: selected, end: selected};
    if (span === "week") { const start = addDays(selected, -dayDate(selected).getUTCDay()); return {start, end: addDays(start, 6)}; }
    return {start: firstDay(), end: lastDay()};
  }
  function range() { const first = firstDay(); const start = addDays(first, -dayDate(first).getUTCDay()); return {start, end: addDays(start, 41)}; }
  const shown = event => (!course || event.definition_id === course) && (!type || eventType(event) === type);
  function visibleEvents() {
    return (data?.events || []).filter(shown).sort((a, b) => clockOrder(a) - clockOrder(b) || a.title.localeCompare(b.title));
  }
  function syncUrl() {
    route = `#calendar?${new URLSearchParams({month, view: mode, date: selected, ...(mode === "agenda" && span !== "day" ? {span} : {})})}`;
    history.replaceState(history.state, "", route);
  }
  function readRoute() {
    if (route === location.hash) return;
    route = location.hash;
    const params = new URLSearchParams(route.split("?")[1] || "");
    const requested = params.get("month");
    month = /^\d{4}-(0[1-9]|1[0-2])$/.test(requested || "") && Number(requested.slice(0, 4)) >= 1900 && Number(requested.slice(0, 4)) <= 9998 ? requested : today.slice(0, 7);
    mode = params.get("view") === "agenda" ? "agenda" : "month";
    span = ["week", "month"].includes(params.get("span")) ? params.get("span") : "day";
    const date = params.get("date") || "";
    selected = /^\d{4}-\d{2}-\d{2}$/.test(date) && date.startsWith(month) && date >= firstDay() && date <= lastDay() ? date : today.startsWith(month) ? today : firstDay();
    signature = "";
    // #calendar?add=1 (Home's quick action) opens Add event once the calendar is loaded; the address drops it.
    if (params.get("add") === "1") { addRequested = true; syncUrl(); }
    // #calendar?google=1 (Workspace settings, and Google's sign-in returning) opens the Google Calendar dialog once.
    if (params.get("google") === "1") { googleRequested = true; syncUrl(); }
  }
  // Google Calendar events are a type of their own only while the sync shows them (the legend and filter name no empty type).
  const googleShown = () => Boolean(data?.google?.permission && data.google.enabled);
  const typeFilters = () => Object.entries(TYPE_FILTERS).filter(([value]) => value !== "google" || googleShown() || type === "google");
  function googleButton() {
    const badge = syncBadge(data?.google);
    return badge ? `<button type="button" class="text-button calendar-google-button" data-calendar-google data-tone="${badge.tone}">${icon("calendar")}<span>Google Calendar</span><small>${e(badge.label)}</small></button>` : "";
  }
  function openGoogle(syncNow = false) {
    dialogIntent += 1;
    void openGoogleCalendar({api, escape, toast, showDialog, dialogHeader, syncNow, changed: () => { void refresh(true); void loadUpcoming(true); }});
  }

  // One event as a row. `sub` rows sit under their group's heading, which already names the title and kind.
  function eventButton(event, sub = false) {
    const kind = eventType(event);
    const where = [sub ? "" : event.code, sub ? "" : event.term_label, event.sections?.length ? `Section${event.sections.length === 1 ? "" : "s"} ${event.sections.join(", ")}` : event.scope === "workspace" ? "Workspace" : "", event.location, event.booking_count ? `${event.booking_count} booking${event.booking_count === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · ");
    const span = event.end_date && event.end_date !== event.date ? `<small>${e(formatDay(event.date, {month: "short", day: "numeric"}))} – ${e(formatDay(event.end_date, {month: "short", day: "numeric"}))}</small>` : "";
    return `<button type="button" class="calendar-event${sub ? " calendar-subevent" : ""}" data-calendar-event="${e(event.id)}" data-kind="${kind}" title="${e([event.time, event.end_time].filter(Boolean).join("–"))}"><span class="calendar-event-time">${e(displayTime(event))}${event.time_basis === "dr_center" ? '<small>DR-center time</small>' : ""}</span><span class="calendar-event-copy">${sub ? "" : `<span class="calendar-kind">${e(TYPE_LABELS[kind] || "Event")}</span><strong>${e(event.title)}</strong>`}${where ? `<small>${e(where)}</small>` : ""}${span}</span><span class="calendar-event-arrow" aria-hidden="true">›</span></button>`;
  }
  // A group of events that belong together (one exam's rooms or DR sections): one heading, then each event under it.
  function groupMarkup(group) {
    if (group.count === 1) return eventButton(group.events[0]);
    const facts = [group.code, ...groupFacts(group), group.time ? `${group.time}${group.dr_time ? " DR-center time" : ""}` : ""].filter(Boolean).join(" · ");
    return `<section class="calendar-group" data-kind="${e(group.type)}" aria-label="${e(`${group.title} · ${TYPE_LABELS[group.type] || "Event"} · ${group.count} events`)}"><header class="calendar-group-head"><span class="calendar-kind">${e(TYPE_LABELS[group.type] || "Event")}</span><strong>${e(group.title)}</strong><small>${e(facts)}</small></header>${[...group.events].sort((a, b) => clockOrder(a) - clockOrder(b) || String(a.location || "").localeCompare(String(b.location || ""), undefined, {numeric: true})).map(event => eventButton(event, true)).join("")}</section>`;
  }
  // A month cell's chip: a single event opens it; a group selects its day, where the group is listed.
  function chipMarkup(group, date) {
    if (group.count === 1) {
      const event = group.events[0];
      return `<button type="button" class="calendar-chip" data-calendar-event="${e(event.id)}" data-kind="${group.type}" title="${e([[event.time, event.end_time].filter(Boolean).join("–"), event.code, event.title].filter(Boolean).join(" · "))}"><span class="calendar-dot" aria-hidden="true"></span><span>${e(event.title)}</span></button>`;
    }
    // Two lines, so the grouping shows without hovering: the title, then what it adds up to ("3 rooms · 7:05 PM").
    const label = chipLabel(group), facts = chipFacts(group);
    return `<button type="button" class="calendar-chip calendar-chip-group" data-calendar-day="${date}" data-kind="${group.type}" title="${e([label, group.code].filter(Boolean).join(" · "))}" aria-label="${e(`${label}: ${group.count} events, show the day`)}"><span class="calendar-dot" aria-hidden="true"></span><span class="calendar-chip-copy"><span>${e(group.title)}</span>${facts.length ? `<small><span class="sr-only"> · </span>${facts.map(fact => `<span>${e(fact)}</span>`).join(" · ")}</small>` : ""}</span></button>`;
  }
  // Next up: the coming groups after campus today, each selecting its own day.
  function nextMarkup() {
    if (selected !== today || !upcoming) return "";
    const groups = nextEvents(upcoming.filter(shown), addDays(today, 1), 5);
    if (!groups.length) return "";
    return `<div class="calendar-next-up"><h4 class="calendar-next-title">Next up</h4><ul role="list">${groups.map(group => `<li><button type="button" class="calendar-next" data-calendar-day="${e(group.date)}" data-kind="${e(group.type)}"><span class="calendar-next-date">${e(wallDate(group.date))}<small>${e(daysFrom(group.date, today))}</small></span><span class="calendar-next-copy"><strong>${e(group.title)}</strong><small>${e([TYPE_LABELS[group.type], group.code, ...groupFacts(group), group.time].filter(Boolean).join(" · "))}</small></span></button></li>`).join("")}</ul></div>`;
  }
  function monthMarkup(events) {
    const {start} = range();
    const cells = Array.from({length: 42}, (_, index) => {
      const date = addDays(start, index), items = events.filter(event => overlaps(event, date)), groups = groupEvents(items);
      return `<td class="calendar-day${date.startsWith(month) ? "" : " calendar-outside"}${date === selected ? " calendar-selected" : ""}"><button type="button" class="calendar-day-number" data-calendar-day="${date}" aria-label="${e(formatDay(date))}, ${items.length} event${items.length === 1 ? "" : "s"}" aria-pressed="${date === selected}" ${date === today ? 'aria-current="date"' : ""}>${Number(date.slice(-2))}</button><div class="calendar-day-events">${groups.slice(0, 2).map(group => chipMarkup(group, date)).join("")}${groups.length > 2 ? `<button type="button" class="calendar-more" data-calendar-day="${date}">+${groups.length - 2} more</button>` : ""}</div></td>`;
    });
    const rows = Array.from({length: 6}, (_, index) => `<tr>${cells.slice(index * 7, index * 7 + 7).join("")}</tr>`).join("");
    const selectedEvents = events.filter(event => overlaps(event, selected));
    const empty = `<p class="calendar-empty">${selected === today ? "Nothing scheduled today." : "Nothing scheduled for this day."}</p>`;
    return `<div class="calendar-month-layout"><div class="calendar-grid-wrap"><table class="calendar-grid" aria-label="Calendar month"><thead><tr>${Array.from({length: 7}, (_, day) => `<th scope="col"><abbr title="${e(formatDay(addDays("2026-09-13", day), {weekday: "long"}))}">${e(formatDay(addDays("2026-09-13", day), {weekday: "short"}))}</abbr></th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div><section class="calendar-day-agenda" aria-labelledby="calendar-day-title"><div class="calendar-day-heading"><span class="eyebrow">${selected === today ? "TODAY" : e(formatDay(selected, {weekday: "long"}))}</span><h3 id="calendar-day-title">${e(formatDay(selected, {month: "long", day: "numeric"}))}</h3><span>${selectedEvents.length} event${selectedEvents.length === 1 ? "" : "s"}</span></div>${selectedEvents.length ? groupEvents(selectedEvents).map(groupMarkup).join("") : empty}${nextMarkup()}<button type="button" class="text-button calendar-day-add" data-calendar-add>${icon("plus")}Add an event</button></section></div>`;
  }
  function agendaMarkup(events) {
    const groups = new Map(), {start, end} = agendaRange();
    events.filter(event => overlaps(event, start, end)).forEach(event => {
      const date = event.date < start ? start : event.date;
      if (!groups.has(date)) groups.set(date, []);
      groups.get(date).push(event);
    });
    const when = {day: selected === today ? "today" : "this day", week: "this week", month: "this month"}[span];
    return `<div class="calendar-agenda">${groups.size ? [...groups].sort(([a], [b]) => a.localeCompare(b)).map(([date, items]) => `<section class="calendar-agenda-day"><h3><span>${e(formatDay(date, {weekday: "short"}))}</span><strong>${Number(date.slice(-2))}</strong><small>${e(formatDay(date, {month: "short"}))}</small></h3><div>${groupEvents(items).map(groupMarkup).join("")}</div></section>`).join("") : `<div class="empty-state calendar-agenda-empty" data-kind="filtered"><h3>No events ${when}</h3><p>No events match ${when} and these filters.</p><button type="button" class="button" data-calendar-add>Add an event</button></div>`}</div>`;
  }
  function periodTitle() {
    if (mode !== "agenda" || span === "month") return formatDay(firstDay(), {month: "long", year: "numeric"});
    if (span === "day") return formatDay(selected, {weekday: "long", month: "long", day: "numeric", year: "numeric"});
    const {start, end} = agendaRange(), short = {month: "short", day: "numeric"};
    return `${formatDay(start, short)} – ${formatDay(end, {...short, year: "numeric"})}`;
  }
  const stepLabel = () => mode === "agenda" && span !== "month" ? span : "month";
  // The compact bar: month and its time note, month navigation, the view switch, then the filters with the color key.
  function barMarkup() {
    return `<div class="calendar-toolbar"><div class="calendar-period"><h2 id="calendar-month-title">${e(periodTitle())}</h2><p class="calendar-time-note">Campus time · Central; DR bookings keep their center’s local time.</p></div><div class="calendar-navigation"><button type="button" class="icon-button" data-calendar-shift="-1" aria-label="Previous ${stepLabel()}">${icon("arrow-left")}</button><button type="button" class="button" data-calendar-today>Today</button><button type="button" class="icon-button" data-calendar-shift="1" aria-label="Next ${stepLabel()}">${icon("arrow-right")}</button><label class="calendar-month-picker"><span class="sr-only">Go to month</span><input type="month" value="${month}" min="1900-01" max="9998-12" aria-label="Go to month"></label></div><div class="calendar-view-switch" role="group" aria-label="Calendar view">${["month", "agenda"].map(view => `<button type="button" data-calendar-mode="${view}" aria-pressed="${mode === view}">${view === "month" ? "Month" : "Agenda"}</button>`).join("")}</div>${mode === "agenda" ? `<div class="calendar-view-switch" role="group" aria-label="Agenda range">${["day", "week", "month"].map(value => `<button type="button" data-calendar-span="${value}" aria-pressed="${span === value}">${value[0].toUpperCase()}${value.slice(1)}</button>`).join("")}</div>` : ""}<div class="calendar-filter-row"><label class="field">Course<select aria-label="Course" data-calendar-course>${option("", "All courses", course)}${data.catalog.courses.map(item => option(item.id, item.code, course)).join("")}</select></label><label class="field">Event type<select aria-label="Event type" data-calendar-type>${option("", "All events", type)}${typeFilters().map(([value, label]) => option(value, label, type)).join("")}</select></label><div class="calendar-legend" aria-label="Event colors">${typeFilters().map(([value, label]) => `<span data-kind="${value}"><i class="calendar-dot" aria-hidden="true"></i>${e(label)}</span>`).join("")}</div>${googleButton()}</div></div>`;
  }
  // The error line: what failed and the one Refresh that retries it. Hidden while the calendar is up to date.
  function errorMarkup() {
    return `<div class="calendar-error" role="alert" data-calendar-error ${loadError ? "" : "hidden"}><span data-calendar-error-text>${e(loadError)}</span><button type="button" class="text-button" data-calendar-refresh>${icon("refresh")}Refresh</button></div>`;
  }
  function paint() {
    if (!active() || !data) return;
    const events = visibleEvents();
    const bounds = range(), ready = loadedRange === `${bounds.start}/${bounds.end}`;
    const body = !ready ? (loadError ? `<p class="calendar-unavailable" role="status">This month’s schedule is unavailable. Refresh to try again.</p>` : loadingMarkup("Loading this month’s schedule…", 3))
      : mode === "month" ? monthMarkup(events) : agendaMarkup(events);
    // Patched, not rebuilt: a poll that changes one event keeps focus, the open select and the scroll position.
    patchChildren(root(), `${barMarkup()}${errorMarkup()}${body}`);
    bind();
    paintHeader();
  }
  // The header names the next thing on the calendar, from the Next up read; the date is the campus date.
  function paintHeader() {
    if (!header || !active()) return;
    const next = upcoming ? nextEvents(upcoming, today, 1)[0] : null;
    // The title names the page (only Home keeps the hero's greeting); the subtitle is the next thing, never a slogan.
    header.set({route: "#calendar", variant: "compact", title: "Calendar", context: `Campus date · ${wallDate(today)}`,
      subtitle: next ? `Next: ${next.title}${next.type === "dr" ? " DR exams" : next.type === "deadline" ? " (deadline)" : ""} · ${wallDate(next.date)}${next.date === today ? " (today)" : ""}`
        : upcoming ? `Nothing scheduled in the next ${NEXT_DAYS} days` : ""});
  }
  async function selectDay(date) {
    selected = date;
    if (!date.startsWith(month)) {
      month = date.slice(0, 7); syncUrl();
      const loading = refresh(true), focus = document.activeElement;
      await loading;
      if (!active() || selected !== date || document.activeElement !== focus) return;
    }
    else { syncUrl(); paint(); }
    root().querySelector(`.calendar-day-number[data-calendar-day="${date}"]`)?.focus({preventScroll: true});
  }
  function shiftMonth(step) {
    const date = dayDate(firstDay()); date.setUTCMonth(date.getUTCMonth() + step);
    if (date.getUTCFullYear() < 1900 || date.getUTCFullYear() > 9998) return;
    month = iso(date).slice(0, 7); selected = today.startsWith(month) ? today : firstDay(); syncUrl(); void refresh(true);
  }
  function goToday() { month = today.slice(0, 7); selected = today; syncUrl(); void refresh(true); }
  function bind() {
    root().querySelectorAll("[data-calendar-shift]").forEach(button => { button.onclick = () => {
      const step = Number(button.dataset.calendarShift);
      if (mode === "agenda" && span !== "month") void selectDay(addDays(selected, step * (span === "week" ? 7 : 1)));
      else shiftMonth(step);
    }; });
    root().querySelectorAll("[data-calendar-span]").forEach(button => { button.onclick = () => { span = button.dataset.calendarSpan; syncUrl(); paint(); root().querySelector(`[data-calendar-span="${span}"]`).focus(); }; });
    root().querySelector("[data-calendar-today]").onclick = goToday;
    const picker = root().querySelector('input[type="month"]');
    if (picker.value !== month && document.activeElement !== picker) picker.value = month;
    picker.onchange = event => {
      if (!event.target.value || !event.target.checkValidity()) return;
      month = event.target.value; selected = today.startsWith(month) ? today : firstDay(); syncUrl(); void refresh(true);
    };
    root().querySelectorAll("[data-calendar-mode]").forEach(button => { button.onclick = () => { mode = button.dataset.calendarMode; syncUrl(); paint(); root().querySelector(`[data-calendar-mode="${mode}"]`).focus(); }; });
    for (const [selector, value, set] of [["[data-calendar-course]", course, next => { course = next; }], ["[data-calendar-type]", type, next => { type = next; }]]) {
      const select = root().querySelector(selector);
      if (select.value !== value) select.value = value; // A patched select keeps the instructor's choice; the state decides.
      select.onchange = event => { set(event.target.value); paint(); root().querySelector(selector).focus(); };
    }
    root().querySelectorAll("[data-calendar-refresh]").forEach(button => { button.onclick = () => void refresh(true); });
    root().querySelectorAll("[data-calendar-day]").forEach(button => { button.onclick = () => selectDay(button.dataset.calendarDay); });
    root().querySelectorAll(".calendar-day-number").forEach(button => { button.onkeydown = event => {
      const step = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7}[event.key];
      if (step) { event.preventDefault(); selectDay(addDays(button.dataset.calendarDay, step)); }
    }; });
    root().querySelectorAll("[data-calendar-event]").forEach(button => { button.onclick = () => openEvent(data.events.find(event => event.id === button.dataset.calendarEvent)); });
    root().querySelectorAll("[data-calendar-add]").forEach(button => { button.onclick = () => void openCreate(); });
    const google = root().querySelector("[data-calendar-google]");
    if (google) google.onclick = () => openGoogle();
  }
  // Next up and the header's "Next:" come from campus today to NEXT_DAYS ahead, read apart from the month on screen and
  // at most once a minute. A failed read changes nothing on screen: Next up is then simply not shown.
  function loadUpcoming(force = false) {
    if (!active() || upcomingPending) return upcomingPending;
    if (!force && upcoming && upcomingDay === today && Date.now() - upcomingAt < NEXT_EVERY) return null;
    const request = ++upcomingGeneration, day = today;
    upcomingPending = (async () => {
      try {
        const result = await api(`/api/calendar?${new URLSearchParams({start: day, end: addDays(day, NEXT_DAYS)})}`);
        if (request !== upcomingGeneration || !active()) return;
        const changed = JSON.stringify(result.events) !== JSON.stringify(upcoming);
        upcoming = result.events || []; upcomingAt = Date.now(); upcomingDay = day;
        if (changed) paint();
      } catch {
        // Next up is optional; the month itself reports its own errors.
      } finally { if (request === upcomingGeneration) upcomingPending = null; }
    })();
    return upcomingPending;
  }
  // The month just read and Next up overlap: when they disagree there (an event added, moved or deleted), Next up is
  // read again at once instead of waiting for its minute.
  function upcomingStale() {
    if (!upcoming || !data) return true;
    const bounds = range(), from = today > bounds.start ? today : bounds.start, horizon = addDays(today, NEXT_DAYS);
    const to = horizon < bounds.end ? horizon : bounds.end;
    if (from > to) return false;
    const slice = events => JSON.stringify(events.filter(event => overlaps(event, from, to)).map(event => JSON.stringify(event)).sort());
    return slice(data.events || []) !== slice(upcoming);
  }
  async function refresh(force = false) {
    if (!active()) return;
    readRoute();
    const bounds = range(), key = `${bounds.start}/${bounds.end}`;
    if (pending?.key === key && !force) return pending.promise;
    const request = ++generation;
    if (data && loadedRange !== key) { loadError = ""; paint(); }
    if (!data) root().innerHTML = loadingMarkup("Opening your calendar…", 3);
    const promise = (async () => {
      try {
        const result = await api(`/api/calendar?${new URLSearchParams(bounds)}`);
        if (request !== generation || !active()) return;
        const recovered = Boolean(loadError);
        data = result; today = data.today; loadedRange = key; loadError = "";
        const next = JSON.stringify([data, month, selected, mode, course, type]);
        if (force || recovered || next !== signature || !root().querySelector(".calendar-toolbar")) { signature = next; paint(); }
        const error = root().querySelector("[data-calendar-error]"); if (error) error.hidden = true;
        void loadUpcoming(upcomingStale());
      } catch (cause) {
        if (request !== generation || !active()) return;
        loadError = `Calendar could not be refreshed. ${cause.message}`;
        if (data) paint();
        let error = root().querySelector("[data-calendar-error]");
        if (!error) {
          // Nothing loaded yet: the error and one way to try again.
          root().innerHTML = `<div class="calendar-error" role="alert" data-calendar-error><span data-calendar-error-text></span></div><button type="button" class="button" data-calendar-retry>Try again</button>`;
          error = root().querySelector("[data-calendar-error]");
          root().querySelector("[data-calendar-retry]").onclick = () => void refresh(true);
        }
        error.querySelector("[data-calendar-error-text]").textContent = loadError; error.hidden = false;
      } finally { if (pending?.request === request) pending = null; }
    })();
    pending = {key, promise, request};
    return promise;
  }
  // The shell calls render() on every refresh of its own; only a changed address or a missing page reads the feed here
  // (the shell's poll calls refresh(), which repaints only what changed). render(true) reads it again at once.
  function render(force = false) {
    if (!active()) return;
    const changed = route !== location.hash;
    readRoute();
    if (force || changed || !root().querySelector(".calendar-toolbar")) void refresh(force);
    else paintHeader();
    if (addRequested) { addRequested = false; void openCreate(); }
    if (googleRequested) { googleRequested = false; openGoogle(true); }
  }
  // Keyboard: T today, P and N the previous and next month (listed in the '?' sheet while the calendar is open).
  for (const [key, label, run] of [["t", "Calendar: today", goToday], ["p", "Calendar: previous month", () => shiftMonth(-1)], ["n", "Calendar: next month", () => shiftMonth(1)]]) {
    registerShortcut({key, label, run, when: () => active() && Boolean(data)});
  }
  function openEvent(event) {
    if (!event) return;
    dialogIntent += 1;
    const dates = event.date === (event.end_date || event.date) ? formatDay(event.date, {weekday: "long", month: "long", day: "numeric", year: "numeric"}) : `${formatDay(event.date, {month: "long", day: "numeric", year: "numeric"})} – ${formatDay(event.end_date, {month: "long", day: "numeric", year: "numeric"})}`;
    const sourceName = {office_hours: "Office hours", exam: "Exams", seating: "Seating charts", dr: "Accommodations", gradebook: "Gradebook", google: "your Google Calendar", schedule: "the Term schedule"}[event.source];
    const sourceLabel = event.source === "dr" ? "Open DR exam schedule" : `Open ${sourceName}`;
    // The stored time text, exactly as its owner keeps it.
    const stored = (event.source === "custom" || event.source === "google" || event.category === "deadline") && event.all_day ? "All day" : event.time ? [event.time, event.end_time].filter(Boolean).join("–") : "Time not set";
    // A Google event opens in Google (a new tab); only a link Google gave is used.
    const googleLink = event.source === "google" && /^https:\/\/(www\.google\.com\/calendar|calendar\.google\.com)\//.test(event.href || "")
      ? `<a class="button primary" href="${e(event.href)}" target="_blank" rel="noopener noreferrer">Open in Google Calendar</a>` : "";
    const origin = event.source === "google" ? `This event comes from your Google Calendar${event.calendar ? ` (${e(event.calendar)})` : ""}. Change it in Google; Faculty shows it as Google has it.`
      : event.source !== "custom" ? `This date comes from ${e(sourceName)}. Open its record to make changes.` : "";
    const hub = event.exam_href && event.exam_href !== event.href ? `<a class="button" data-calendar-source href="${e(event.exam_href)}">Open exam</a>` : "";
    showDialog(`${dialogHeader(e(event.title), e(TYPE_LABELS[eventType(event)] || "Event"))}<div class="dialog-body calendar-event-detail"><p class="calendar-detail-date">${e(dates)}</p><p class="section-lead">${e(stored)} <span class="calendar-detail-zone">${event.time_basis === "dr_center" ? "· DR-center local time" : "· Campus time (Central)"}</span></p><dl class="course-facts">${[["Course", event.code], ["Term", event.term_label], ["Sections", event.sections?.join(", ")], ["Location", event.location], ["Bookings", event.booking_count], ["Calendar", event.source === "google" ? event.calendar : ""]].filter(([, value]) => value).map(([label, value]) => `<div><dt>${label}</dt><dd>${e(value)}</dd></div>`).join("")}</dl>${event.notes ? `<div class="calendar-notes">${e(event.notes)}</div>` : ""}${origin ? `<p class="supporting-copy">${origin}</p>` : ""}</div><footer class="dialog-footer">${event.editable ? '<button type="button" class="text-button danger-button" data-calendar-delete>Delete event</button><button type="button" class="button primary" data-calendar-edit>Edit event</button>' : googleLink || (event.href?.startsWith("#") ? `${hub}<a class="button primary" data-calendar-source href="${e(event.href)}">${e(sourceLabel)}</a>` : hub)}<button type="button" class="button" data-action="close">Close</button></footer>`);
    const dialog = document.getElementById("dialog");
    dialog.querySelector("[data-calendar-edit]")?.addEventListener("click", () => openForm(event));
    dialog.querySelector("[data-calendar-delete]")?.addEventListener("click", () => deleteEvent(event));
    dialog.querySelectorAll("[data-calendar-source]").forEach(link => link.addEventListener("click", () => dialog.close()));
  }
  async function openCreate() {
    const intent = ++dialogIntent, dialog = document.getElementById("dialog");
    const content = dialog.firstElementChild, wasOpen = dialog.open;
    await refresh();
    if (!active() || !data || loadError || intent !== dialogIntent || dialog.firstElementChild !== content || dialog.open !== wasOpen) return;
    openForm(null);
  }
  function openForm(event) {
    dialogIntent += 1;
    const saved = event || {date: selected, end_date: selected, all_day: true, category: "event", scope: course ? "course" : "workspace", definition_id: course};
    const requestId = crypto.randomUUID();
    showDialog(`<form id="calendar-event-form">${dialogHeader(event ? "Edit event" : "Add to your calendar", "Campus local dates and times · Central")}<div class="dialog-body"><label class="field">Title<input name="title" maxlength="160" value="${e(saved.title || "")}" required autofocus></label><div class="calendar-form-pair"><label class="field">Type<select name="category">${option("event", "Event", saved.category)}${option("deadline", "Deadline", saved.category)}</select></label><label class="field">Scope<select name="scope">${["workspace", "course", "term", "section"].map(scope => option(scope, scope[0].toUpperCase() + scope.slice(1), saved.scope)).join("")}</select></label></div><div class="detail-group calendar-scope-fields"><label class="field" data-calendar-scope="course">Course<select name="definition_id">${option("", "Choose a course")}${data.catalog.courses.map(item => option(item.id, item.code, saved.definition_id)).join("")}</select></label><label class="field" data-calendar-scope="term">Term<select name="term_id"></select></label><label class="field" data-calendar-scope="section">Section<select name="section_id"></select></label></div><div class="calendar-form-pair"><label class="field">Date<input type="date" name="date" value="${e(saved.date)}" required></label><label class="field">End date<input type="date" name="end_date" value="${e(saved.end_date || saved.date)}" required></label></div><label class="checkbox-field"><input type="checkbox" name="all_day" ${saved.all_day ? "checked" : ""}>All day</label><div class="calendar-form-pair calendar-time-fields"><label class="field">Time<input type="time" name="time" value="${e(saved.time || "")}"></label><label class="field">End time<input type="time" name="end_time" value="${e(saved.end_time || "")}"></label></div><label class="field">Location <span class="field-optional">optional</span><input name="location" maxlength="240" value="${e(saved.location || "")}"></label><label class="field">Notes <span class="field-optional">optional</span><textarea name="notes" maxlength="4000" rows="3">${e(saved.notes || "")}</textarea></label>${errorLine()}</div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Save event</button></footer></form>`);
    const form = document.getElementById("calendar-event-form"), fields = form.elements;
    const current = dialogSession(form, document.getElementById("dialog"));
    function scopes(reset = false, initial = false) {
      const scope = fields.scope.value, definition = fields.definition_id.value;
      const term = reset ? "" : (initial ? saved.term_id : fields.term_id.value);
      const section = reset ? "" : (initial ? saved.section_id : fields.section_id.value);
      fields.term_id.innerHTML = option("", "Choose a term") + data.catalog.terms.filter(item => item.definition_id === definition).map(item => option(item.id, item.term_label, term)).join("");
      fields.section_id.innerHTML = option("", "Choose a section") + data.catalog.sections.filter(item => item.term_id === fields.term_id.value).map(item => option(item.id, `Section ${item.number}`, section)).join("");
      form.querySelector(".calendar-scope-fields").hidden = scope === "workspace";
      for (const [key, enabled] of [["course", scope !== "workspace"], ["term", ["term", "section"].includes(scope)], ["section", scope === "section"]]) {
        const wrapper = form.querySelector(`[data-calendar-scope="${key}"]`), select = wrapper.querySelector("select");
        wrapper.hidden = !enabled; select.disabled = !enabled; select.required = enabled;
      }
    }
    fields.scope.onchange = () => scopes(); fields.definition_id.onchange = () => scopes(true); fields.term_id.onchange = () => scopes(); scopes(false, true);
    function timing() { const allDay = fields.all_day.checked; form.querySelector(".calendar-time-fields").hidden = allDay; fields.time.disabled = fields.end_time.disabled = allDay; fields.time.required = !allDay; }
    fields.all_day.onchange = timing; timing();
    fields.date.onchange = () => { if (fields.end_date.value < fields.date.value) fields.end_date.value = fields.date.value; fields.end_date.min = fields.date.value; };
    fields.end_date.min = fields.date.value;
    form.onsubmit = submission => {
      submission.preventDefault();
      const payload = {title: fields.title.value, category: fields.category.value, scope: fields.scope.value, date: fields.date.value, end_date: fields.end_date.value, all_day: fields.all_day.checked, time: fields.all_day.checked ? "" : fields.time.value, end_time: fields.all_day.checked ? "" : fields.end_time.value, location: fields.location.value, notes: fields.notes.value, definition_id: fields.definition_id.disabled ? null : fields.definition_id.value, term_id: fields.term_id.disabled ? null : fields.term_id.value, section_id: fields.section_id.disabled ? null : fields.section_id.value, ...(event ? {version: event.version} : {request_id: requestId})};
      void runFormAction(form, async () => {
        await api(event ? `/api/calendar/events/${encodeURIComponent(event.custom_id)}` : "/api/calendar/events", event ? "PUT" : "POST", payload);
        if (!current()) return;
        document.getElementById("dialog").close(); toast(event ? "Event updated." : "Event added.");
        month = payload.date.slice(0, 7); selected = payload.date; course = ""; type = ""; syncUrl(); await refresh(true);
      });
    };
  }
  function deleteEvent(event) {
    dialogIntent += 1;
    showDialog(`<form id="calendar-delete-form">${dialogHeader("Delete this event?", e(event.title))}<div class="dialog-body"><p>Remove this custom event from your calendar?</p>${errorLine()}</div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button danger">Delete event</button></footer></form>`);
    const form = document.getElementById("calendar-delete-form"), current = dialogSession(form, document.getElementById("dialog"));
    form.onsubmit = submission => { submission.preventDefault(); void runFormAction(form, async () => {
      await api(`/api/calendar/events/${encodeURIComponent(event.custom_id)}`, "DELETE", {version: event.version});
      if (!current()) return;
      document.getElementById("dialog").close(); toast("Event deleted."); await refresh(true);
    }); };
  }
  return {render, refresh, openCreate};
}
