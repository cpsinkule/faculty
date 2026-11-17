// @ts-check
// Excused absences (absences/, AGENTS.md "Excused absences"): the page #absences lists the absences students ask to have excused,
// with their documentation and your decision; a student's card (the gradebook's record, the roster's student window) shows theirs
// and records a new one. An excused absence gives the iClicker sessions within its dates full credit in the gradebook, and changes
// nothing else there or in the exam records (requirement, 2026-10-01; ABSENCES ICLICKER-CREDIT). The address carries only filters
// (status, course, scope), never a student. Recording or deciding an absence composes no email here; the Makeups view lists the
// makeups excused absences make owed (ABSENCES "MAKEUPS", absence-makeups.js) and carries the controls that ask the server to
// email a booking card (ABSENCES "MAKEUP BOOKING"), which the server also sends by itself when the term's cards are on.
import { captureDialogIntent, dialogSession, disclosureMarkup, emptyMarkup, errorMessage, filterChipsMarkup, bindFilterChips, loadingMarkup, localToday, runFormAction, wallDate } from "./ui.js";
import { patchChildren } from "./dom-patch.js";
import { studentPicker } from "./student-picker.js";
import { viewLink } from "./file-viewer.js";
import { aheadText, daysMarkup, plural, splitText, totalsMarkup } from "./absence-totals.js";
import { makeupsMarkup, scheduleMakeup, workMarkup, makeupGroups, sendCard, switchBooking, holdMakeups, releaseHold, departmentSignInMarkup, missedMakeup } from "./absence-makeups.js";
import { openGoogleSignIn } from "./aim-signin.js";
import { CAMPUS_ZONE } from "./calendar-feed.js";

const BASE = "/api/absences";
const STATUS_TONES = { pending: "warning", excused: "success", not_excused: "neutral", withdrawn: "neutral" };
const VIEWS = ["makeups", "totals"];  // the page's views besides the absences (Makeups owed; TOTAL ABSENCES › ON THE ABSENCES PAGE)
const DECISIONS = /** @type {const} */ ([["pending", "Waiting"], ["excused", "Excused"], ["not_excused", "Not excused"], ["withdrawn", "Withdrawn"]]);
// RULE 7 NOTICE (requirement, 2026-10-05): the list's Notice column, from the server's `notice.timing`.
const NOTICE_SHORT = { advance: "In advance", after: "After it began", late: "Late", none: "None on record" };
const MOMENT_FORMAT = new Intl.DateTimeFormat("en-US", { timeZone: CAMPUS_ZONE, weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

/** "Mon, Oct 5, 3:20 PM" on the campus wall clock, from a UTC instant; "" when it cannot be read. @param {string} iso */
export function campusMoment(iso) {
  const moment = new Date(String(iso || ""));
  return Number.isNaN(moment.getTime()) ? "" : MOMENT_FORMAT.format(moment);
}

/** The Notice column's cell: late notice is marked, the rest read as words. @param {any} notice @param {(value: unknown) => string} e */
export function noticeCell(notice, e) {
  const timing = notice?.timing || "none";
  const short = NOTICE_SHORT[/** @type {keyof typeof NOTICE_SHORT} */ (timing)] || NOTICE_SHORT.none;
  if (timing === "late") return `<span class="chip" data-tone="warning" title="${e(`After the end of ${wallDate(notice.due)}, the second business day after the absence`)}">${e(short)}</span>`;
  if (timing === "none") return `<span class="field-hint">${e(short)}</span>`;
  return timing === "after" ? `<span title="On time only when notice could not come before the absence">${e(short)}</span>` : e(short);
}

/** What a save changed in the gradebook's iClicker columns (the answer's `iclicker_credit`), as a toast's tail; "" for nothing.
 * @param {{filled?: number, put_back?: number} | undefined} credit */
export function creditText(credit) {
  const sessions = (/** @type {number} */ n) => `${n} iClicker session${n === 1 ? "" : "s"}`;
  return [credit?.filled ? `full credit for ${sessions(credit.filled)}` : "", credit?.put_back ? `credit taken back from ${sessions(credit.put_back)}` : ""]
    .filter(Boolean).join(", ");
}

export { plural, splitText, aheadText };

/** The page's filters from its address: status, course and scope (current or all terms). The page opens on the absences Waiting for
 * a decision; "all" shows every decision (requirement, 2026-10-08).
 * @param {string} hash */
export function readRoute(hash) {
  const params = new URLSearchParams(String(hash || "").split("?")[1] || "");
  const status = params.get("status") || "";
  return { status: status === "all" || DECISIONS.some(([key]) => key === status) ? status : "pending", course: params.get("course") || "", scope: params.get("scope") === "all" ? "all" : "current",
    view: VIEWS.includes(params.get("view") || "") ? String(params.get("view")) : "" };
}

/** @param {{status?: string, course?: string, scope?: string, view?: string}} route */
export function routeHash({ status = "pending", course = "", scope = "current", view = "" }) {
  const params = new URLSearchParams();
  if (VIEWS.includes(view)) params.set("view", view);
  if (status && status !== "pending") params.set("status", status);
  if (course) params.set("course", course);
  if (scope === "all") params.set("scope", "all");
  const query = params.toString();
  return `#absences${query ? `?${query}` : ""}`;
}

/** The absences a status ("" or "all" for every one), course and search leave (every word typed, in any order, in the name, course,
 * reason or dates).
 * @param {any[]} absences @param {{status?: string, course?: string, search?: string}} filters */
export function filterAbsences(absences, { status = "", course = "", search = "" } = {}) {
  const words = String(search || "").toLowerCase().split(/\s+/).filter(Boolean);
  return absences.filter((item) => (!status || status === "all" || item.status === status) && (!course || item.code === course)
    && words.every((word) => `${item.name} ${item.code} ${item.section} ${item.reason_label} ${item.dates}`.toLowerCase().includes(word)));
}

/**
 * @param {{api: Function, escape: (value: unknown) => string, toast: Function, showDialog: (markup: string) => void,
 *   dialogHeader: (title: string, subtitle?: string) => string, header?: any}} deps
 */
export function createAbsences({ api, escape, toast, showDialog, dialogHeader, header = null }) {
  const e = (/** @type {unknown} */ value) => escape(String(value ?? ""));
  const dialog = () => /** @type {HTMLDialogElement} */ (document.getElementById("dialog"));
  const root = () => /** @type {HTMLElement | null} */ (document.getElementById("absences-section"));
  let data = /** @type {any} */ (null), search = "", generation = 0, bound = false, lastHash = "";
  let route = readRoute(location.hash);

  function setHeader() {
    header?.set({ route: location.hash, title: "Absences", subtitle: "Absences students ask you to excuse, their documentation and your decision.",
      crumbs: [{ label: "Home", href: "#overview" }, { label: "Absences" }],
      action: { label: "Record an absence", icon: "plus", run: () => openNew({}) } });
  }

  // The shell renders the page, then again after its state read, and every 30 s: an unforced render joins the read under way
  // (FRONTEND.md "One read per navigation"), and a read that finds the same absences changes nothing on screen, so the table
  // keeps its scroll and the search its caret (FRONTEND.md "Quiet refresh contract"; it was redrawn whole, twice, on every poll).
  /** @type {{scope: string, promise: Promise<void>} | null} */
  let loading = null;
  let signature = "";

  async function render(force = false) {
    const host = root();
    if (!host) return;
    setHeader();
    const routeChanged = location.hash !== lastHash;
    if (routeChanged) { route = readRoute(location.hash); lastHash = location.hash; }
    if (!host.querySelector("[data-absences-frame]")) {
      host.innerHTML = `<div class="absences-page" data-absences-frame><div data-absences-head></div><div data-absences-list>${loadingMarkup("Loading absences…", 4)}</div></div>`;
      bind(host);
      signature = "";
      if (data) draw();
    } else if (data && routeChanged) draw();
    if (!force && loading && loading.scope === route.scope) return loading.promise;
    const run = ++generation, scope = route.scope;
    const promise = (async () => {
      try {
        const query = scope === "all" ? "?scope=all" : "";
        // A failed makeups read leaves the absences shown (and the Makeups view empty until the next read).
        // TOTAL ABSENCES: every student's total is read only while its view is shown (it counts every student's days).
        const [next, makeups, google, totals] = await Promise.all([api(`${BASE}${query}`), api(`${BASE}/makeups${query}`).catch(() => ({ makeups: data?.makeups || [] })),
          api(`${BASE}/department-makeups/google${data ? "" : "?check=1"}`).catch(() => data?.department_google || null),
          route.view === "totals" ? api(`${BASE}/totals${query}`).catch(() => data?.totals || null) : Promise.resolve(data?.totals || null)]);
        if (run !== generation) return;
        next.totals = totals && totals.scope === scope ? totals : null;
        next.makeups = makeups.makeups || [];
        next.department_google = google;  // GOOGLE SIGN-IN for department makeup requests
        next.makeups_booking = makeups.booking || data?.makeups_booking || null;  // MAKEUP BOOKING: switches, versions
        const text = JSON.stringify(next);
        data = next;
        if (text === signature || root()?.hidden) return;
        signature = text;
        draw();
      } catch (cause) {
        if (run !== generation) return;
        signature = "";  // a failed read: the next good one draws again, changed or not
        const list = host.querySelector("[data-absences-list]");
        if (list && !data) list.innerHTML = `<div class="callout" data-tone="danger" role="alert"><p>${e(errorMessage(cause))}</p><button type="button" class="button" data-absences-retry>Try again</button></div>`;
      }
    })();
    loading = { scope, promise };
    try { await promise; } finally { if (loading?.promise === promise) loading = null; }
  }

  function draw() {
    const host = root();
    if (!host || !data) return;
    const head = /** @type {HTMLElement} */ (host.querySelector("[data-absences-head]"));
    const list = /** @type {HTMLElement} */ (host.querySelector("[data-absences-list]"));
    const inScope = data.absences;
    const courses = [...new Set(inScope.map((/** @type {any} */ item) => item.code))].sort();
    const byCourse = filterAbsences(inScope, { course: route.course });
    const makeups = (data.makeups || []).filter((/** @type {any} */ row) => !route.course || row.code === route.course);
    const views = filterChipsMarkup("absence-view", [{ value: "", label: "Absences", count: byCourse.length },
      { value: "makeups", label: "Makeups owed", count: makeups.filter((/** @type {any} */ row) => row.state === "owed").length },
      { value: "totals", label: "Totals by student" }], route.view, { label: "Show" });
    // Waiting first, the page's landing, then All and the settled decisions.
    const counted = DECISIONS.map(([key, label]) => ({ value: key, label, count: byCourse.filter((/** @type {any} */ item) => item.status === key).length }));
    const chips = route.view ? "" : filterChipsMarkup("absence-status", [counted[0], { value: "all", label: "All", count: byCourse.length }, ...counted.slice(1)],
      route.status, { label: "Decision" });
    // ONE NAVIGATION (2026-10-05, requirement: these pages "feel hard to use and not intuitive"): the two views are tabs on a row of
    // their own; the decision filter, search and terms are the toolbar under them.
    patchChildren(head, `${views.replace('class="filter-chips"', 'class="filter-chips sub-tabs"')}<div class="absences-toolbar">${chips}
      <label class="field absences-search">Find a student<input id="absences-search" type="search" data-page-search placeholder="Name, course, reason or date" autocomplete="off" value="${e(search)}"></label>
      ${courses.length > 1 || route.course ? `<label class="field">Course<select id="absences-course"><option value="">All courses</option>${courses.map((code) => `<option value="${e(code)}"${code === route.course ? " selected" : ""}>${e(code)}</option>`).join("")}</select></label>` : ""}
      <label class="field">Terms<select id="absences-scope"><option value="current"${route.scope === "current" ? " selected" : ""}>Current terms</option><option value="all"${route.scope === "all" ? " selected" : ""}>All terms</option></select></label></div>`);
    // What waits on the instructor (§1.2): the undecided absences' and the owed makeups' counts are the orange block when not 0.
    head.querySelector('[data-filter-group="absence-status"] [data-filter="pending"]')?.toggleAttribute("data-needs", byCourse.some((/** @type {any} */ item) => item.status === "pending"));
    head.querySelector('[data-filter-group="absence-view"] [data-filter="makeups"]')?.toggleAttribute("data-needs", makeups.some((/** @type {any} */ row) => row.state === "owed"));
    // A patched control keeps what the instructor typed or chose; the filters in memory are what the page shows.
    const box = /** @type {HTMLInputElement | null} */ (head.querySelector("#absences-search"));
    if (box && box.value !== search) box.value = search;
    const courseSelect = /** @type {HTMLSelectElement | null} */ (head.querySelector("#absences-course"));
    if (courseSelect) courseSelect.value = route.course;
    const scopeSelect = /** @type {HTMLSelectElement | null} */ (head.querySelector("#absences-scope"));
    if (scopeSelect) scopeSelect.value = route.scope;
    if (route.view === "makeups") {
      const words = search.toLowerCase().split(/\s+/).filter(Boolean);
      const found = makeups.filter((/** @type {any} */ row) => words.every((word) => `${row.name} ${row.code} ${row.section} ${row.title} ${row.dates}`.toLowerCase().includes(word)));
      patchChildren(list, makeupsMarkup(found, e, emptyMarkup, data.makeups_booking) + departmentSignInMarkup(data.department_google, e));
      return;
    }
    if (route.view === "totals") {
      if (!data.totals) {
        patchChildren(list, loadingMarkup("Counting each student’s absences…", 4));
        void loadTotals();
        return;
      }
      patchChildren(list, totalsMarkup(data.totals, { course: route.course, search }, { e, emptyMarkup }));
      return;
    }
    const shown = filterAbsences(inScope, { status: route.status, course: route.course, search });
    if (!shown.length) {
      patchChildren(list, inScope.length && route.status === "pending" && !route.course && !search.trim()
        ? emptyMarkup({ icon: "calendar", title: "Nothing waits for your decision", body: "Every absence in these terms is decided or withdrawn.",
          action: { label: "Show all absences", data: { absencesClear: "" } } })
        : inScope.length
        ? emptyMarkup({ icon: "search", kind: "filtered", title: "No absences match these filters", body: "Change the filters or clear them to see every absence.", action: { label: "Clear filters", data: { absencesClear: "" } } })
        : emptyMarkup({ icon: "calendar", title: "No absences recorded yet", body: "When a student asks to have an absence excused, record it here with their documentation and your decision.",
          action: { label: "Record an absence", data: { absencesNew: "" }, primary: true } }));
      return;
    }
    // One line a row: the section alone while one course of the current terms is shown (its code and term are then the same on every
    // row), the reason's details cut to one line (all of it in the absence's window and the hint); only a decision that waits for the
    // instructor is marked (§1.2), a settled one reads as words.
    const oneCourse = courses.length <= 1 && route.scope === "current";
    const rows = shown.map((/** @type {any} */ item) => `<tr>
        <td data-label="Student"><button type="button" class="absences-name" data-absence="${e(item.id)}" aria-haspopup="dialog">${e(item.name || "Student")}<span class="sr-only">: open the absence</span></button></td>
        <td data-label="${oneCourse ? "Section" : "Course & section"}">${oneCourse ? e(item.section) : `${e(item.code)} · ${e(item.section)}<small>${e(item.term_label)}</small>`}</td>
        <td data-label="Dates">${e(item.dates)}</td>
        <td data-label="Reason">${e(item.reason_label)}${item.details ? `<small class="absences-details" title="${e(item.details)}">${e(item.details)}</small>` : ""}</td>
        <td data-label="Notice">${noticeCell(item.notice, e)}</td>
        <td data-label="Documentation">${item.documents ? e(`${item.documents} file${item.documents === 1 ? "" : "s"}`) : '<span class="field-hint">None</span>'}</td>
        <td data-label="Decision">${item.status === "pending" ? `<span class="chip" data-tone="warning">${e(item.status_label)}</span>` : `<span class="absences-decision" data-status="${e(item.status)}">${e(item.status_label)}</span>`}${item.past_deadline ? ' <span class="chip" data-tone="warning" title="Documented and still undecided after the third business day after the absence">Past deadline</span>' : ""}</td></tr>`).join("");
    patchChildren(list, `<p class="absences-count section-lead" aria-live="polite">${e(`${shown.length} absence${shown.length === 1 ? "" : "s"}`)}</p>
      <div class="roster-table-scroll absences-table-scroll" tabindex="0" role="region" aria-label="Absences; scroll to see all columns"><table class="roster-table absences-table">
      <thead><tr><th scope="col">Student</th><th scope="col">${oneCourse ? "Section" : "Course &amp; section"}</th><th scope="col">Dates</th><th scope="col">Reason</th><th scope="col">Notice</th><th scope="col">Documentation</th><th scope="col">Decision</th></tr></thead><tbody>${rows}</tbody></table></div>`);
  }

  /** TOTAL ABSENCES: every student's total of the page's terms, read when its view is first shown (the page's refresh reads it again
   * while the view stays). */
  let totalsLoading = /** @type {Promise<void> | null} */ (null);
  function loadTotals() {
    if (totalsLoading) return totalsLoading;
    const scope = route.scope;
    totalsLoading = (async () => {
      try {
        const totals = await api(`${BASE}/totals${scope === "all" ? "?scope=all" : ""}`);
        if (data && route.scope === scope) { data.totals = totals; signature = ""; draw(); }
      } catch (cause) {
        const list = root()?.querySelector("[data-absences-list]");
        if (list && route.view === "totals") list.innerHTML = `<div class="callout" data-tone="danger" role="alert"><p>${e(errorMessage(cause))}</p></div>`;
      } finally { totalsLoading = null; }
    })();
    return totalsLoading;
  }

  /** Keep the address true to the filters without a navigation (no student in it). */
  function syncRoute() {
    const hash = routeHash(route);
    if (hash !== location.hash) history.replaceState(history.state, "", `${location.pathname}${location.search}${hash}`);
    lastHash = location.hash;
  }

  /** @param {HTMLElement} host */
  function bind(host) {
    if (bound) return;
    bound = true;
    host.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      const open = target.closest("[data-absence]");
      if (open) { void openAbsence(open.getAttribute("data-absence") || ""); return; }
      if (target.closest("[data-absences-new]")) { openNew({}); return; }
      const schedule = target.closest("[data-makeup-schedule]");
      if (schedule) {
        const group = makeupGroups(data?.makeups || []).find((item) => item.event_id === schedule.getAttribute("data-makeup-schedule"));
        if (group) void scheduleMakeup({ api, escape, toast, showDialog, dialogHeader }, group, changed);
        return;
      }
      const cardsSwitch = target.closest("[data-makeup-cards]");
      if (cardsSwitch) {
        void switchBooking({ api, escape, toast, showDialog, dialogHeader }, { termId: cardsSwitch.getAttribute("data-makeup-cards") || "",
          revision: Number(cardsSwitch.getAttribute("data-makeup-revision")) || 0, to: cardsSwitch.getAttribute("data-makeup-cards-to") === "off" ? "off" : "on",
          rows: data?.makeups || [] }, changed);
        return;
      }
      if (target.closest("[data-department-signin]")) {
        void openGoogleSignIn({ api, showDialog, dialogHeader, escape }).then((user) => {
          if (user) toast(`Signed in to Google as ${user}`);
          void render(true);
        });
        return;
      }
      const card = target.closest("[data-makeup-card]");
      if (card instanceof HTMLElement) { void sendCard({ api, toast }, card, changed); return; }
      // MAKEUP HOLDS: Hold… / Change… and Release on a quiz row (the hold covers every makeup of that absence).
      const hold = target.closest("[data-makeup-hold]");
      if (hold instanceof HTMLElement) {
        const row = (data?.makeups || []).find((/** @type {any} */ item) => item.absence_id === hold.dataset.makeupHold && item.hold);
        holdMakeups({ api, escape, toast, showDialog, dialogHeader }, hold, row, changed);
        return;
      }
      const release = target.closest("[data-makeup-release]");
      if (release instanceof HTMLElement) { void releaseHold({ api, toast }, release, changed); return; }
      // MISSED (2026-10-31): "Missed makeup…" asks, then records the 0.
      const missed = target.closest("[data-missed-event]");
      if (missed instanceof HTMLElement) {
        const row = (data?.makeups || []).find((/** @type {any} */ item) => item.event_id === missed.dataset.missedEvent
          && item.section_id === missed.dataset.missedSection && item.enrollment_id === missed.dataset.missedEnrollment);
        if (row) missedMakeup({ api, escape, toast, showDialog, dialogHeader }, row, changed);
        return;
      }
      const draft = target.closest("[data-makeup-draft]");
      if (draft) {
        void import("./makeup-drafts.js").then(({ openMakeupDrafts }) => openMakeupDrafts({ api, escape, toast, showDialog, dialogHeader },
          { planId: draft.getAttribute("data-makeup-draft") || "", title: draft.getAttribute("data-makeup-title") || "" }));
        return;
      }
      if (target.closest("[data-absences-retry]")) { void render(true); return; }
      if (target.closest("[data-absences-clear]")) { route = { ...route, status: "all", course: "" }; search = ""; syncRoute(); draw(); }
    });
    host.addEventListener("input", (event) => {
      const target = event.target;
      if (target instanceof HTMLInputElement && target.id === "absences-search") { search = target.value; draw(); }
    });
    host.addEventListener("change", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLSelectElement)) return;
      if (target.id === "absences-course") { route = { ...route, course: target.value }; syncRoute(); draw(); }
      if (target.id === "absences-scope") { route = { ...route, scope: target.value === "all" ? "all" : "current" }; syncRoute(); void render(true); }
    });
    bindFilterChips(host, "absence-status", (value) => { route = { ...route, status: value }; syncRoute(); draw(); });
    bindFilterChips(host, "absence-view", (value) => { route = { ...route, view: VIEWS.includes(value) ? value : "" }; syncRoute(); draw(); });
  }

  /** The page is drawn again after a change made in a dialog, when it is the page shown. */
  function changed() {
    if (root() && !root()?.hidden) void render(true);
  }

  /** "Back to <name>": the footer's way back to the student's card a window was opened from (rest-people, 2026-10-02); "" without one.
   * @param {Back | null | undefined} back */
  const backMarkup = (back) => back ? `<button type="button" class="button" data-dialog-back>${e(back.label)}</button>` : "";
  /** @param {HTMLElement} form @param {Back | null | undefined} back */
  const bindBack = (form, back) => {
    const button = /** @type {HTMLElement | null} */ (form.querySelector("[data-dialog-back]"));
    if (button && back) button.onclick = () => back.run();
  };

  // -- Recording a new absence ----------------------------------------------------------------------------------------------
  /** @param {{sectionId?: string, enrollmentId?: string, name?: string, section?: number|string, back?: Back | null}} who @param {any} [draft] */
  async function openNew(who, draft = null) {
    const intended = captureDialogIntent(dialog());
    const known = Boolean(who.sectionId && who.enrollmentId);
    let groups = [];
    if (!known) {
      try { groups = (await api(`${BASE}/students`)).groups || []; } catch (cause) { toast(errorMessage(cause), true); return; }
    }
    if (!intended()) return;
    const picker = known ? null : studentPicker(escape, {
      id: "absence-student", name: "student", label: "Student", value: "",
      groups: groups.map((/** @type {any} */ group) => ({ label: `${group.code} · ${group.term_label}`,
        options: group.students.map((/** @type {any} */ student) => ({ value: `${student.section_id}:${student.enrollment_id}`,
          text: `${student.name} · section ${student.section}`, note: student.dropped ? "dropped" : "" })) })),
      search: async (digits) => {
        const found = await api(`${BASE}/students/search`, "POST", { digits });
        return (found.students || []).map((/** @type {any} */ s) => ({ value: `${s.section_id}:${s.enrollment_id}`, text: `${s.name} · section ${s.section_number}`, note: `${Math.round(s.similarity * 100)}% match` }));
      },
    });
    const today = localToday();  // the campus date, never UTC's (from 7 pm CDT that was already tomorrow)
    const facts = draft?.facts || { first_date: today, last_date: today, reason: "illness", details: "", status: "pending", decision_note: "" };
    const documents = draft?.documents || [];
    showDialog(`<form class="absence-form" id="absence-new-form" novalidate>${dialogHeader("Record an absence", known ? `${e(who.name || "")} · Section ${e(who.section ?? "")}` : "")}
      <div class="dialog-body">${picker ? `<label class="field">Student${picker.markup}</label>` : ""}
        ${draft ? `<p class="field-hint">Drafted from “${e(draft.subject || "this email")}". Review the details and choose your decision before recording.</p>
          ${draft.verification ? `<p class="field-hint">${e(draft.verification.detail)}</p>` : ""}
          ${(facts.warnings || []).length ? `<div class="callout" data-tone="warning"><ul>${facts.warnings.map((/** @type {string} */ note) => `<li>${e(note)}</li>`).join("")}</ul></div>` : ""}` : ""}
        ${factsMarkup(facts)}
        ${draft?.notice_message_id ? `<input type="hidden" name="notice_message_id" value="${e(draft.notice_message_id)}"><p class="field-hint">This email is kept as their notice; the absence's window says whether it came on time.</p>` : ""}
        ${documents.length ? `<fieldset><legend>Documentation from this email</legend><p class="field-hint">Selected files are added when you record the absence.</p>
          ${documents.map((/** @type {any} */ file, /** @type {number} */ index) => `<label><input type="checkbox" data-mail-document="${index}" checked> ${e(file.name)} · ${e(sizeText(file.size))}</label>`).join("<br>")}</fieldset>` : ""}
        <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer">${backMarkup(who.back)}<button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Record absence</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.getElementById("absence-new-form"));
    const session = dialogSession(form, dialog());
    picker?.bind(form);
    bindBack(form, who.back);
    let requestId = "";
    form.addEventListener("input", () => { requestId = ""; });
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        const fields = /** @type {any} */ (form.elements);
        const [sectionId, enrollmentId] = known ? [who.sectionId, who.enrollmentId] : String(fields.student?.value || "").split(":");
        if (!sectionId || !enrollmentId) throw new Error("Choose the student.");
        if (draft && (!fields.first_date.value || !fields.last_date.value || !fields.reason.value)) throw new Error("Fill in both dates and choose a reason before recording the draft.");
        requestId ||= crypto.randomUUID();
        const saved = await api(BASE, "POST", { section_id: sectionId, enrollment_id: enrollmentId, request_id: requestId, ...readFacts(form) });
        const failures = [];
        for (const box of form.querySelectorAll("[data-mail-document]:checked")) {
          const index = Number(/** @type {HTMLElement} */ (box).dataset.mailDocument), file = documents[index];
          const body = { request_id: `maildoc:${requestId}:${index}` };
          try {
            if (file.message_id) {
              await api(`${BASE}/${encodeURIComponent(saved.id)}/documents/message`, "POST", { ...body, message_id: file.message_id, index: file.index });
            } else {
              await api(`${BASE}/${encodeURIComponent(saved.id)}/documents/mail`, "POST", { ...body, thread_id: draft.thread_id, message_id: draft.message_id, part: file.part }, {}, 120000);
            }
          } catch (cause) { failures.push(`${file.name}: ${errorMessage(cause)}`); }
        }
        const credit = creditText(saved.iclicker_credit);
        toast(credit ? `Absence recorded · ${credit}` : "Absence recorded");
        changed();
        if (session()) {
          if (draft && !failures.length) dialog().close();
          // The new absence's window: its Back says a save happened (the card reads again what the absence changed).
          else await openAbsence(saved.id, documents.length ? null : saved, failures, who.back ? { label: who.back.label, run: () => who.back?.run({ saved: true }) } : null);
        }
      });
    };
    /** @type {HTMLElement | null} */ (form.querySelector(known ? "[name=first_date]" : "[data-picker-text]"))?.focus();
  }

  /** @param {any} facts */
  function factsMarkup(facts) {
    const reasons = data?.reasons || REASON_LABELS;
    return `<div class="absence-dates"><label class="field">First day<input type="date" name="first_date" value="${e(facts.first_date)}" required></label>
        <label class="field">Last day<input type="date" name="last_date" value="${e(facts.last_date)}" required></label></div>
      <label class="field">Reason<select name="reason">${!facts.reason ? '<option value="" selected>Choose a reason</option>' : ""}${Object.entries(reasons).map(([key, label]) => `<option value="${e(key)}"${key === facts.reason ? " selected" : ""}>${e(label)}</option>`).join("")}</select></label>
      <label class="checkbox-field"><input type="checkbox" name="death"${facts.death ? " checked" : ""}>A death: never marked Not excused automatically</label>
      <label class="field">Details <span class="field-optional">optional</span><textarea name="details" rows="2" maxlength="2000" placeholder="What the student told you">${e(facts.details)}</textarea></label>
      <fieldset class="absence-decision"><legend>Your decision</legend><div class="absence-decision-choices">${DECISIONS.map(([key, label]) => `<label><input type="radio" name="status" value="${key}"${key === facts.status ? " checked" : ""}>${e(label)}</label>`).join("")}</div>
        <label class="field">Note on your decision <span class="field-optional">optional</span><input name="decision_note" maxlength="2000" value="${e(facts.decision_note)}" placeholder="e.g. Doctor's note received"></label>
        <p class="field-hint">Excused gives every iClicker session within these dates full credit in the gradebook, and makes a makeup owed for work given on these days.</p></fieldset>`;
  }

  /** @param {HTMLFormElement} form */
  function readFacts(form) {
    const fields = /** @type {any} */ (form.elements);
    const status = /** @type {HTMLInputElement | null} */ (form.querySelector("[name=status]:checked"))?.value || "pending";
    return { first_date: fields.first_date.value, last_date: fields.last_date.value || fields.first_date.value, reason: fields.reason.value,
      details: fields.details.value, status, decision_note: fields.decision_note.value, death: Boolean(fields.death?.checked),
      ...(fields.notice_message_id ? { notice_message_id: fields.notice_message_id.value } : {}) };
  }

  // -- One absence --------------------------------------------------------------------------------------------------------
  /** One absence: its facts, documentation and decision. Opened from a student's card, `back` returns there, and does so after a save.
   * @param {string} absenceId @param {any} [known] @param {string[]} [documentFailures] @param {Back | null} [back] */
  async function openAbsence(absenceId, known = null, documentFailures = [], back = null) {
    const intended = captureDialogIntent(dialog());
    let absence = known;
    if (!absence) {
      try { absence = await api(`${BASE}/${encodeURIComponent(absenceId)}`); } catch (cause) { toast(errorMessage(cause), true); return; }
    }
    if (!intended()) return;
    showDialog(`<form class="absence-form absence-detail" id="absence-form" novalidate>${dialogHeader(e(absence.name || "Student"), e(`${absence.code} · Section ${absence.section} · ${absence.term_label}`))}
      <div class="dialog-body" data-absence-body></div>
      <footer class="dialog-footer">${backMarkup(back)}<button type="button" class="button" data-action="close">Close</button><button type="submit" class="button" data-absence-save>Save changes</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.getElementById("absence-form"));
    const body = /** @type {HTMLElement} */ (form.querySelector("[data-absence-body]"));
    const session = dialogSession(form, dialog());
    bindBack(form, back);
    let requestId = "", dirty = false;
    /** @type {{message_id: string, at: string, subject: string} | null} */
    let chosen = null;  // RULE 7 NOTICE: a message chosen in the picker and not saved yet ({message_id: ""}: none on record)

    /** The Notice part: when the notice came against Rule 7 (as saved), or the message chosen and not saved yet. */
    function noticeMarkup() {
      const notice = absence.notice || { timing: "none", label: "No notice on record" };
      const value = chosen ? chosen.message_id : absence.notice_message_id || "";
      const saved = notice.timing === "none" ? `<p>${e(notice.label)}</p>`
        : `<p>${e(campusMoment(notice.at))} · ${notice.timing === "late" ? `<span class="chip" data-tone="warning">${e(notice.label)}</span>` : e(notice.label)}</p>`;
      const pending = chosen ? `<p>${chosen.message_id ? e(`Chosen: ${chosen.subject || "their message"}, ${campusMoment(chosen.at)}.`) : "No notice on record."} Save changes to keep it.</p>` : saved;
      const rule = absence.documentation_due && notice.due
        ? `<p class="field-hint">${e(`Rule 7: notice before the absence, or by the end of ${wallDate(notice.due)} when it could not come before; documentation by the end of ${wallDate(absence.documentation_due)}.`)}</p>` : "";
      return `<section class="absence-section" aria-labelledby="absence-notice-title"><h3 id="absence-notice-title">Notice</h3>${pending}${rule}
        <input type="hidden" name="notice_message_id" value="${e(value)}">
        <div class="absence-doc-actions"><button type="button" class="button" data-notice-pick>Choose their message…</button></div>
        <div data-notice-picker></div></section>`;
    }

    function draw() {
      const documents = absence.documents || [];
      const standing = documents.filter((/** @type {any} */ doc) => !doc.removed);
      const removed = documents.filter((/** @type {any} */ doc) => doc.removed);
      const credit = absence.iclicker || [];
      const docRow = (/** @type {any} */ doc) => `<li><a ${viewLink(doc.url, doc.name, e)}>${e(doc.name)}</a> <small>${e(doc.kind === "message" ? "from their messages" : "uploaded")} · ${e(sizeText(doc.size))}</small>
        <button type="button" class="text-button" data-doc-${doc.removed ? "restore" : "remove"}="${e(doc.id)}">${doc.removed ? "Restore" : "Remove"}</button></li>`;
      // A redraw after adding or removing a document keeps what is typed and not yet saved.
      body.innerHTML = `${documentFailures.length ? `<div class="callout" data-tone="warning"><p>The absence was recorded, but these files could not be added. Add them in Documentation below.</p><ul>${documentFailures.map(note => `<li>${e(note)}</li>`).join("")}</ul></div>` : ""}${factsMarkup(dirty ? { ...absence, ...readFacts(form) } : absence)}
        ${noticeMarkup()}
        <section class="absence-section" aria-labelledby="absence-docs-title"><h3 id="absence-docs-title">Documentation</h3>
          ${standing.length ? `<ul class="absence-docs">${standing.map(docRow).join("")}</ul>` : '<p class="field-hint">No documentation yet.</p>'}
          <div class="absence-doc-actions"><label class="button absence-upload">Upload a file<input type="file" data-doc-upload accept="application/pdf,image/jpeg,image/png,image/heic,image/webp" class="sr-only"></label>
            <button type="button" class="button" data-doc-pick>Pick from their messages…</button></div>
          <div data-doc-picker></div>
          ${removed.length ? `<details class="disclosure"><summary>Removed <span class="disclosure-hint">${removed.length}</span></summary><ul class="absence-docs">${removed.map(docRow).join("")}</ul></details>` : ""}</section>
        ${credit.length ? `<section class="absence-section" aria-labelledby="absence-iclicker-title" data-absence-iclicker><h3 id="absence-iclicker-title">iClicker</h3>
          <p class="field-hint">Full credit in the gradebook for this excused absence:</p>
          <ul class="absence-docs">${credit.map((/** @type {any} */ cell) => `<li>${e(cell.title)}${cell.date ? ` <small>${e(wallDate(cell.date))}</small>` : ""} <small>${e(cell.points)} points</small></li>`).join("")}</ul></section>` : ""}
        ${workMarkup(absence.work || [], e)}
        ${(absence.revisions || []).length > 1 ? `<details class="disclosure absence-history"><summary>History</summary><ul>${(absence.revisions || []).map((/** @type {any} */ revision) =>
          `<li>${e(wallDate(revision.created_at.slice(0, 10), { year: true }))}: ${e(revision.dates)} · ${e(revision.reason_label)} · ${e(revision.status_label)}${revision.decision_note ? ` · “${e(revision.decision_note)}”` : ""}</li>`).join("")}</ul></details>` : ""}
        <p class="form-error" role="alert" hidden></p>`;
    }

    draw();
    form.addEventListener("input", (event) => {
      if (/** @type {HTMLElement} */ (event.target).closest("[data-doc-picker], [data-notice-picker]")) return;
      requestId = "";
      if (dirty) return;
      dirty = true;
      /** @type {HTMLElement} */ (form.querySelector("[data-absence-save]")).classList.add("primary");
    });
    form.addEventListener("change", (event) => {
      const target = /** @type {HTMLInputElement} */ (event.target);
      if (target.matches("[data-doc-upload]") && target.files?.length) void upload(target.files[0]);
    });
    form.addEventListener("click", (event) => {
      const target = /** @type {HTMLElement} */ (event.target);
      const remove = target.closest("[data-doc-remove], [data-doc-restore]");
      if (remove) {
        const action = remove.hasAttribute("data-doc-remove") ? "remove" : "restore";
        const id = remove.getAttribute(`data-doc-${action}`) || "";
        void act(() => api(`${BASE}/${encodeURIComponent(absence.id)}/documents/${encodeURIComponent(id)}/${action}`, "POST", { request_id: crypto.randomUUID() }),
          action === "remove" ? "Document removed" : "Document restored");
        return;
      }
      if (target.closest("[data-doc-pick]")) { void pickMessage(); return; }
      const unmiss = target.closest("[data-unmiss-event]");
      if (unmiss instanceof HTMLElement) {
        // MISSED's Undo: the makeup is owed again.
        void act(async () => {
          await api("/api/absences/makeups/missed", "POST", { request_id: crypto.randomUUID(), missed: false, event_id: unmiss.dataset.unmissEvent,
            section_id: unmiss.dataset.unmissSection, enrollment_id: unmiss.dataset.unmissEnrollment });
          return api(`${BASE}/${encodeURIComponent(absence.id)}`);
        }, "Makeup owed again");
        return;
      }
      if (target.closest("[data-notice-pick]")) { void pickNotice(); return; }
      const notice = target.closest("[data-notice]");
      if (notice) {
        chosen = { message_id: notice.getAttribute("data-notice") || "", at: notice.getAttribute("data-notice-at") || "", subject: notice.getAttribute("data-notice-subject") || "" };
        const field = /** @type {HTMLInputElement} */ (form.querySelector("[name=notice_message_id]"));
        field.value = chosen.message_id;
        field.dispatchEvent(new Event("input", { bubbles: true }));  // unsaved, as any typed change: Save changes keeps it
        draw();
        return;
      }
      const pick = target.closest("[data-pick]");
      if (pick) {
        const [messageId, index] = String(pick.getAttribute("data-pick")).split("|");
        void act(() => api(`${BASE}/${encodeURIComponent(absence.id)}/documents/message`, "POST", { message_id: messageId, index: Number(index), request_id: crypto.randomUUID() }),
          "Attachment added");
      }
    });
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        requestId ||= crypto.randomUUID();
        const next = await api(`${BASE}/${encodeURIComponent(absence.id)}`, "POST", { base_revision: absence.revision, request_id: requestId, ...readFacts(form) });
        const same = next.revision === absence.revision;
        absence = next;
        requestId = "";
        dirty = false;
        chosen = null;
        if (!session()) return;
        /** @type {HTMLElement} */ (form.querySelector("[data-absence-save]")).classList.remove("primary");
        draw();
        const credit = creditText(next.iclicker_credit);
        toast(same ? "Nothing changed" : credit ? `Absence saved · ${credit}` : "Absence saved");
        if (!same) changed();
        // Opened from a student's card: a saved decision goes back to the card, which reads their absences again (and the
        // gradebook's record its View: an excused absence fills iClicker cells, ICLICKER-CREDIT).
        if (!same && back) back.run({ saved: true });
      });
    };

    /** @param {() => Promise<any>} write @param {string} message */
    async function act(write, message) {
      await runFormAction(form, async () => {
        absence = await write();
        if (!session()) return;
        draw();
        toast(message);
        changed();
      });
    }

    /** @param {File} file */
    async function upload(file) {
      if (file.size > 20 * 1024 * 1024) { toast("That file is larger than 20 MB.", true); return; }
      const payload = new FormData();
      payload.append("file", file);
      payload.append("request_id", crypto.randomUUID());
      await act(() => api(`${BASE}/${encodeURIComponent(absence.id)}/documents`, "POST", payload, {}, 120000), "Document added");
    }

    /** RULE 7 NOTICE: their own messages, newest first, to choose the one that first told you about the absence. */
    async function pickNotice() {
      const host = /** @type {HTMLElement} */ (body.querySelector("[data-notice-picker]"));
      host.innerHTML = loadingMarkup("Reading their messages…", 2);
      try {
        const timeline = await api(`/api/communications/students/${encodeURIComponent(absence.enrollment_id)}`);
        const theirs = (timeline.entries || []).filter((/** @type {any} */ entry) => entry.author_role === "student" && entry.message_id).slice(0, 20);
        if (!session()) return;
        host.innerHTML = `${theirs.length ? `<ul class="absence-pick">${theirs.map((/** @type {any} */ entry) => `<li><button type="button" class="text-button" data-notice="${e(entry.message_id)}" data-notice-at="${e(entry.at)}" data-notice-subject="${e(entry.subject || "")}">${e(entry.subject || entry.channel_label || "Their message")}</button>
            <small>${e(campusMoment(entry.at))}${entry.channel_label ? ` · ${e(entry.channel_label)}` : ""}</small></li>`).join("")}</ul>`
          : '<p class="field-hint">They have no kept messages.</p>'}${absence.notice_message_id || chosen?.message_id ? '<button type="button" class="text-button" data-notice="">No notice on record</button>' : ""}`;
      } catch (cause) {
        if (session()) host.innerHTML = `<p class="form-error" role="alert">${e(errorMessage(cause))}</p>`;
      }
    }

    async function pickMessage() {
      const host = /** @type {HTMLElement} */ (body.querySelector("[data-doc-picker]"));
      host.innerHTML = loadingMarkup("Reading their messages…", 2);
      try {
        const timeline = await api(`/api/communications/students/${encodeURIComponent(absence.enrollment_id)}`);
        const files = (timeline.entries || []).flatMap((/** @type {any} */ entry) => (entry.attachments || [])
          .filter((/** @type {any} */ file) => file.kept !== false && entry.message_id)
          .map((/** @type {any} */ file) => ({ entry, file })));
        if (!session()) return;
        host.innerHTML = files.length
          ? `<ul class="absence-pick">${files.map((/** @type {{entry: any, file: any}} */ { entry, file }) => `<li><button type="button" class="text-button" data-pick="${e(entry.message_id)}|${e(file.index)}">${e(file.name || "attachment")}</button>
              <small>${e(entry.subject || entry.channel_label || "")} · ${e(wallDate(String(entry.at).slice(0, 10), { year: true }))}</small></li>`).join("")}</ul>`
          : '<p class="field-hint">None of their kept messages has an attachment.</p>';
      } catch (cause) {
        if (session()) host.innerHTML = `<p class="form-error" role="alert">${e(errorMessage(cause))}</p>`;
      }
    }
  }

  /** Draft from the exact message selected in Mail. Closing/replacing the dialog stops its engine; a late answer never opens a form.
   * @param {{thread_id: string, message_id: string}} source */
  async function draftFromMail(source) {
    showDialog(`<form id="absence-draft-form">${dialogHeader("Draft absence from email", "")}
      <div class="dialog-body">${loadingMarkup("Reading the email with your selected assistant…", 3)}<p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button></footer></form>`);
    const form = /** @type {HTMLFormElement} */ (document.getElementById("absence-draft-form"));
    const alive = dialogSession(form, dialog());
    let id = "", timer = 0;
    const finish = () => {
      clearTimeout(timer);
      if (id) { const closed = id; id = ""; void api(`/api/assistant/conversations/${closed}/close`, "POST", {}).catch(() => {}); }
    };
    dialog().addEventListener("close", finish, { once: true });
    const show = async (/** @type {any} */ view) => {
      if (!alive()) { finish(); return; }
      if (view.busy) {
        timer = window.setTimeout(() => void poll(), 900);
        return;
      }
      finish();
      if (view.error || !view.draft) throw new Error(view.error || "The assistant did not return a draft. Record the absence manually or try again.");
      const draft = view.draft, student = draft.student;
      await openNew({ sectionId: student.section_id, enrollmentId: student.enrollment_id, name: student.name, section: student.section }, draft);
    };
    const failed = (/** @type {unknown} */ cause) => {
      finish();
      if (!alive()) return;
      const host = /** @type {HTMLElement} */ (form.querySelector(".dialog-body"));
      host.innerHTML = `<div class="callout" data-tone="danger" role="alert"><p>${e(errorMessage(cause))}</p>
        <button type="button" class="button" data-draft-retry>Try again</button> <a class="text-button" href="#grading?settings=1" data-action="close">Compute settings</a></div>`;
      host.querySelector("[data-draft-retry]")?.addEventListener("click", () => void draftFromMail(source));
    };
    const poll = async () => {
      if (!alive()) { finish(); return; }
      try { await show(await api(`/api/assistant/conversations/${encodeURIComponent(id)}`)); } catch (cause) { failed(cause); }
    };
    try {
      const view = await api(`${BASE}/drafts/mail`, "POST", source, {}, 120000);
      id = view.id;
      await show(view);
    } catch (cause) { failed(cause); }
  }

  // -- A student's card ---------------------------------------------------------------------------------------------------
  /**
   * TOTAL ABSENCES (requirement, 2026-10-31; absences/totals.py): the days the student missed, each counted once whether an absence,
   * an iClicker 0 or a quiz 0 says so, split by decision, with the days themselves in a closed disclosure.
   * @param {any} total the student read's `total`
   */
  function totalMarkup(total) {
    if (!total) return "";
    const split = splitText(total);
    const body = daysMarkup(total, e);
    return `<div class="absence-total"><p class="absence-total-line"><strong>${e(total.total ? plural(total.total, "absence") : "No absences")}</strong>${split ? ` <span>${e(split)}</span>` : ""}</p>
      ${body ? disclosureMarkup({ label: "Days missed", hint: total.total, body, className: "absence-total-days" }) : ""}</div>`;
  }

  /**
   * The Absences part of a student's card: their total of days missed, their absences in that section (each opens it) and Record
   * an absence. Reads once. `who.back` returns from the absence's window to the card.
   * @param {HTMLElement} host @param {{sectionId: string, enrollmentId: string, name?: string, section?: number|string, back?: Back | null}} who
   */
  async function panel(host, who) {
    host.innerHTML = '<p class="field-hint">Reading absences…</p>';
    try {
      const result = await api(`${BASE}/students/${encodeURIComponent(who.sectionId)}/${encodeURIComponent(who.enrollmentId)}`);
      if (!host.isConnected) return;
      const items = result.absences || [];
      host.innerHTML = `${totalMarkup(result.total)}${items.length ? `<ul class="absence-card-list">${items.map((/** @type {any} */ item) => `<li><button type="button" class="text-button" data-card-absence="${e(item.id)}">${e(item.dates)}</button>
          <span>${e(item.reason_label)}</span> <span class="chip" data-tone="${STATUS_TONES[/** @type {keyof typeof STATUS_TONES} */ (item.status)] || "neutral"}">${e(item.status_label)}</span></li>`).join("")}</ul>`
        : '<p class="field-hint">No absences recorded.</p>'}<button type="button" class="button" data-card-absence-new>Record an absence…</button>`;
      host.onclick = (event) => {
        const target = /** @type {HTMLElement} */ (event.target);
        const open = target.closest("[data-card-absence]");
        if (open) void openAbsence(open.getAttribute("data-card-absence") || "", null, [], who.back || null);
        else if (target.closest("[data-card-absence-new]")) void openNew({ ...who, section: who.section ?? result.section });
      };
    } catch (cause) {
      if (host.isConnected) host.innerHTML = `<p class="field-hint">${e(errorMessage(cause))}</p>`;
    }
  }

  return { render, openNew, openAbsence, panel, draftFromMail };
}

/** @typedef {{label: string, run: (options?: {saved?: boolean}) => void}} Back  A way back to the student's card a window was opened
 * from; `saved` says an absence was recorded or changed on the way, so the card reads again what it shows. */

// Before the page has read the server's list, a card's dialog still names the reasons (the server's REASONS, in its order).
const REASON_LABELS = {
  illness: "Illness", family: "Death or major illness in the immediate family", dependent: "Illness of a dependent family member",
  activity: "University-authorized activity", religious: "Religious holy day", legal: "Legal or administrative proceeding", military: "Military duty",
  interview: "Graduate or professional school interview", pregnancy: "Pregnancy or a related condition", other: "Other",
};

/** "1.2 MB" @param {number} bytes */
export function sizeText(bytes) {
  const size = Number(bytes) || 0;
  return size < 1024 ? `${size} B` : size < 1048576 ? `${Math.round(size / 1024)} KB` : `${(size / 1048576).toFixed(1)} MB`;
}
