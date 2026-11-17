// Course pages: the course list (Current and All courses), the course/term/section workspace with its work hub, the
// section roster and the Import roster dialogs. app.js keeps the course settings dialog and the shell. Course data is the
// shell's /api/state (state.courseGroups); per-section counts come from GET /api/course-groups/summary (catalog only);
// rosters are read from their section route; the work hub reads existing list GETs. Nothing here decides or writes a
// derived state: counts and links are views of saved records (UX overhaul, 2026-09-26).
import { firstName } from "./communications.js";
import { scheduleText, accommodationTags } from "./accommodations.js";
import { openCanvasSignIn, openHowdyRosters } from "./aim-signin.js";
import { disclosureMarkup, emptyMarkup, filterChipsMarkup, bindFilterChips, loadingMarkup, wallDate, localToday, daysFrom, setBusy, dialogSession, captureDialogIntent } from "./ui.js";
import { issueLabel } from "./response-card-label.js";
import { scaleSummary } from "./grading-scheme.js";

const $ = (selector) => document.querySelector(selector);

export function courseTerm(course) {
  return [
    course.term === "Other" ? course.term_other : course.term,
    course.year,
  ]
    .filter(Boolean)
    .join(" ");
}

export function groupCurrentLabel(group) {
  return group.is_current
    ? group.all_current
      ? "Current"
      : "Partly current"
    : "Not current";
}

export function storageDate(value) {
  return value
    ? new Date(value).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : "";
}

export function storagePending(course) {
  return [
    "restore_pending",
    "sync_pending",
    "archive_pending",
    "purge_pending",
  ].includes(course.storage_status);
}

/** '85 students', '1 student', or 'No roster yet' for one section's counts. */
export function studentsLabel(counts) {
  if (!counts) return "";
  return counts.students ? `${counts.students.toLocaleString()} student${counts.students === 1 ? "" : "s"}` : "No roster yet";
}

/** The section tile's line: '85 students · 1 with accommodations · 2 DR bookings'. */
export function sectionDetail(counts) {
  if (!counts) return "";
  if (!counts.students) return "No roster yet · import it on the section page";
  return [
    studentsLabel(counts),
    counts.with_accommodations ? `${counts.with_accommodations.toLocaleString()} with accommodations` : "",
    counts.dr_bookings ? `${counts.dr_bookings.toLocaleString()} DR booking${counts.dr_bookings === 1 ? "" : "s"}` : "",
  ].filter(Boolean).join(" · ");
}

/**
 * The roster's summary line: '85 students · 1 with accommodations · 1 DR booking · Imported Sun, Sep 13 (13 days ago)
 * from roster.csv · 85 added · 0 updated'. `today` is the campus date (YYYY-MM-DD) for the relative age.
 */
export function rosterSummary(data, today = localToday()) {
  const parts = [`${data.count.toLocaleString()} student${data.count === 1 ? "" : "s"}`];
  if (data.accommodation_count) parts.push(`${data.accommodation_count.toLocaleString()} with accommodations`);
  if (data.dr_exam_count) parts.push(`${data.dr_exam_count.toLocaleString()} DR booking${data.dr_exam_count === 1 ? "" : "s"}`);
  if (data.dropped_count) parts.push(`${data.dropped_count.toLocaleString()} dropped`);
  const latest = data.last_import;
  if (latest) {
    const at = new Date(latest.at);
    const day = Number.isNaN(at.getTime()) ? "" : localToday(at);
    const age = day ? daysFrom(day, today) : "";
    parts.push(`Imported ${day ? wallDate(day) : "earlier"}${age && age !== "today" ? ` (${age})` : age === "today" ? " (today)" : ""}${latest.filename ? ` from ${latest.filename}` : ""}`);
    parts.push(`${latest.added} added`, `${latest.updated} updated`);
  }
  return parts.join(" · ");
}

/** A roster cell as read: the registrar's export marks some values bold with Markdown ("**Web Registered**"; usability audit,
 * 2026-10-04), which the cell shows without the asterisks. The imported value, its search and sort are unchanged. Pure. */
export const rosterValue = (/** @type {unknown} */ value) => String(value ?? "").trim().replace(/\*\*(.+?)\*\*/gs, "$1");

/** Roster columns in class-list order: name, UIN and email first, then the others; empty columns reported apart. */
export function rosterColumns(headers, students) {
  const preferred = ["name", "uin", "email"];
  const rank = (header) => preferred.includes(header.toLowerCase()) ? preferred.indexOf(header.toLowerCase()) : preferred.length;
  const ordered = [...headers].sort((a, b) => rank(a) - rank(b));
  const filled = (header) => students.some((student) => String(student.fields?.[header] ?? "").trim() !== "");
  return {
    lead: ordered.filter((header) => rank(header) < preferred.length),
    rest: ordered.filter((header) => rank(header) === preferred.length && filled(header)),
    empty: ordered.filter((header) => rank(header) === preferred.length && !filled(header)),
  };
}

/** The section a roster file's name names (requirement, 2026-10-15), or "": the one section of the course whose number stands alone among the name's runs of digits
 * ("MATH-140-507_202631-10-15-2026.xlsx" → 507). None or several → "", and the instructor chooses. */
export function sectionFromFileName(name, sections) {
  const numbers = new Set(String(name).replace(/\.[^.]*$/, "").split(/\D+/).filter(Boolean).map(Number));
  const found = sections.filter((section) => numbers.has(Number(section)));
  return found.length === 1 ? String(found[0]) : "";
}

/** One line naming the students a roster import says are leaving (rosters.leaving): "Leaving: Lara Castillo, Greta (Q drop)." */
export function leavingText(leaving) {
  return `Leaving: ${leaving.map((row) => `${row.name} (${row.reason})`).join("; ")}.`;
}

/** A section's files for 'Import rosters': a list of sections chosen for files → the first duplicate, or null. */
export function duplicateSection(choices) {
  const seen = new Set();
  for (const choice of choices) {
    if (!choice) continue;
    if (seen.has(choice)) return choice;
    seen.add(choice);
  }
  return null;
}

/**
 * The work hub's rows for one term (and, on a section tab, one section) from the existing list reads. A seating chart or a Response
 * Card issue of an assessment with certified grades (`archived`, CERTIFIED-ARCHIVE) is left out of its row, which counts it in
 * `archived` instead (the row then links "N archived" to its page); a row is dropped only when nothing is left in it. Card issues
 * read as on their page (`issueLabel`); on the course Home, `sectionNumbers` ({section_id: number}) adds the sections each names.
 */
export function workRows({ termId, sectionId = null, sectionNumber = null, sectionNumbers = null, grading = null, exams = null, seating = null, cards = null,
  definitionId = null, currentTerm = true }) {
  const rows = [];
  // ASSIGNMENT-WORKSPACE: exams and grading are listed under Assignments (a bare #exams or #grading opens its unfiltered list),
  // so "N more" opens that list for this course, on its Archived tab (earlier terms, by course) when this course page is not a current term.
  const assignments = `#assignments${definitionId ? `?course=${encodeURIComponent(definitionId)}${currentTerm ? "" : "&status=archived"}` : ""}`;
  const forSection = (ids, numbers = []) => sectionId === null || (ids || []).includes(sectionId) || (numbers || []).includes(sectionNumber);
  const push = (kind, all, item, more) => {
    const live = all.filter((entry) => !entry.archived);
    const archived = all.length - live.length;
    if (live.length || archived) rows.push({ kind, items: live.map(item), more, ...(archived ? { archived } : {}) });
  };
  if (exams) {
    const items = exams.filter((exam) => exam.term_id === termId && forSection((exam.sections || []).map((section) => section.id)))
      .map((exam) => ({ label: exam.title || exam.assignment_title || "Exam", href: `#exams/${encodeURIComponent(exam.id)}` }));
    if (items.length) rows.push({ kind: "Exams", items, more: assignments });
  }
  if (seating) {
    push("Seating charts", seating.filter((chart) => chart.term_id === termId && forSection(chart.section_ids, chart.sections)),
      (chart) => ({ label: [chart.title, chart.room_name].filter(Boolean).join(" · "), href: `#seating/${encodeURIComponent(chart.id)}`, note: chart.confirmed_at ? "" : "not confirmed" }), "#seating");
  }
  if (grading) {
    const items = grading.filter((plan) => plan.term_id === termId)
      .map((plan) => ({ label: plan.title || "Assignment", href: `#grading/${encodeURIComponent(plan.id)}` }));
    if (items.length) rows.push({ kind: sectionId === null ? "Grading" : "Grading · whole term", items, more: assignments });
  }
  if (cards) {
    // On the course Home, the sections an issue names tell apart sets whose titles read alike ('Exam 1 · Disability Resource
    // Center' was sent to three sections); a section tab lists only its own, and a course of one section needs no suffix.
    const numbered = sectionId === null && sectionNumbers && Object.keys(sectionNumbers).length > 1;
    const where = (issue) => {
      if (!numbered) return "";
      const numbers = [...new Set((issue.section_ids || issue.context?.section_ids || []).map((id) => sectionNumbers[id]).filter(Boolean))].sort((a, b) => a - b);
      return numbers.length ? ` · Section${numbers.length > 1 ? "s" : ""} ${numbers.join(", ")}` : "";
    };
    // Each set of cards sent, by its title, opening that issue ("2 issues" read as two problems beside Exams and Grading).
    push("Response Cards", cards.filter((issue) => (issue.term_id || issue.context?.term_id) === termId && forSection(issue.section_ids || issue.context?.section_ids)),
      (issue) => ({ label: `${issue.title ? issueLabel(issue) : "Response cards"}${where(issue)}`, href: `#response-cards/issue/${encodeURIComponent(issue.id)}` }), "#response-cards");
  }
  return rows;
}

const WORK_WORDS = { "Exams": ["exam", "exams"], "Seating charts": ["seating chart", "seating charts"], "Grading": ["in grading", "in grading"],
  "Grading · whole term": ["in grading", "in grading"], "Response Cards": ["card set", "card sets"] };

/** A closed work list's counts in a line: '4 exams · 1 seating chart · 4 in grading · 2 card sets · 3 archived'. */
export function workCounts(rows) {
  const parts = rows.filter((row) => row.items.length).map((row) => {
    const [one, many] = WORK_WORDS[row.kind] || [row.kind.toLowerCase(), row.kind.toLowerCase()];
    return `${row.items.length.toLocaleString()} ${row.items.length === 1 ? one : many}`;
  });
  const archived = rows.reduce((sum, row) => sum + (row.archived || 0), 0);
  if (archived) parts.push(`${archived.toLocaleString()} archived`);
  return parts.join(" · ") || "Nothing yet";
}

/** One line on where a term's Canvas content stands (a row of GET /api/canvas/overview), for the course Home's Canvas row. */
export function canvasStatusText(term) {
  const courses = term.courses || [];
  const pushed = courses.filter((course) => course.pushed).length;
  const unlinked = (term.sections || []).filter((section) => !section.course).length;
  const missing = unlinked ? ` · ${unlinked} section${unlinked === 1 ? "" : "s"} without a Canvas course` : "";
  if (!courses.length) return "Link each section’s Canvas course first";
  if (!term.copy) return `Not started${missing}`;
  if (!pushed) return `Not pushed yet${missing}`;
  return `Pushed to ${pushed} of ${courses.length} Canvas course${courses.length === 1 ? "" : "s"}${missing}`;
}

/** A `mailto:` link to one or more TAs with the section in the subject. Addresses are the server's checked ones; each is still
 * percent-encoded except its "@", so no character can end the address list or start the query. */
export function taMailto(emails, subject = "") {
  const to = emails.map((email) => encodeURIComponent(email).replace(/%40/g, "@")).join(",");
  return `mailto:${to}${subject ? `?subject=${encodeURIComponent(subject)}` : ""}`;
}

/** The subject a TA email starts with: the course code and section ("MATH 140 Section 507"), which the instructor can change. */
export function taSubject(view) {
  return [view.code, view.section != null ? `Section ${view.section}` : ""].filter(Boolean).join(" ");
}

/** Copy from another section: its TAs added after the ones in the form, skipping anyone already there (same email, or same name
 * when neither has an email). Returns the merged list and how many were added. */
export function mergeTAs(current, incoming) {
  const key = (ta) => (ta.email ? `e:${ta.email.trim().toLowerCase()}` : `n:${(ta.name || "").trim().toLowerCase()}`);
  const kept = current.filter((ta) => Object.values(ta).some((value) => String(value || "").trim()));
  const seen = new Set(kept.map(key));
  const added = incoming.filter((ta) => !seen.has(key(ta)) && seen.add(key(ta)));
  return { contacts: [...kept, ...added.map(({ name = "", email = "", phone = "", note = "" }) => ({ name, email, phone, note }))], added: added.length };
}

// The list page's frame (formerly static markup in index.html). Every id stays; `courseToggle` adds the page's own
// Current | All switch, which carries the inventory counts #current-courses-count and #all-courses-count, in one toolbar
// with the search box.
function listFrame(icon, courseToggle) {
  return `<div class="toolbar course-toolbar">${courseToggle ? `<nav class="course-scope-toggle" aria-label="Courses shown"><a href="#current-courses" data-course-scope="current-courses" aria-label="Current courses">Current courses<span id="current-courses-count" class="nav-count" data-course-count="current">0</span></a><a href="#all-courses" data-course-scope="all-courses" aria-label="All courses">All courses<span id="all-courses-count" class="nav-count" data-course-count="all">0</span></a></nav>` : ""}<div class="search-box">
              <span data-icon="search">${icon("search")}</span>
              <input
                type="search"
                id="course-search"
                placeholder="Find a course…"
                aria-label="Find a course"
                autocomplete="off"
              />
              <kbd>/</kbd>
            </div><span id="course-list-count" class="toolbar-count" role="status"></span></div>
          <div id="course-storage-summary" aria-live="polite"></div>
          <div
            id="courses-grid"
            class="tools-grid courses-grid"
          ></div>`;
}

const SHOW_EMPTY_KEY = "faculty:roster:show-empty-columns";
function readShowEmpty() {
  try { return globalThis.localStorage?.getItem(SHOW_EMPTY_KEY) === "1"; } catch { return false; }
}
function writeShowEmpty(value) {
  try { globalThis.localStorage?.setItem(SHOW_EMPTY_KEY, value ? "1" : "0"); } catch { /* a convenience only */ }
}

/** The hosts a rebuilt course page hands back to the page they were on while its data-host-key is unchanged (the roster is kept apart). */
const KEPT_HOSTS = ["[data-gradebook-host]", "[data-canvas-content-host]", "[data-course-notes-host]", "[data-question-bank-host]", "[data-term-schedule-host]", "[data-class-schedule-host]"];

/**
 * The course pages. `state` is the shell's state object (read here, and state.query is set by the list's search box);
 * `saveForm` is the shell's dialog save helper; `courseResources(group, section)` lists the Inbox resources of a course page.
 */
export function createCourses({ api, state, escape, icon, toast, showDialog, dialogHeader, saveForm, courseResources, header = null, courseToggle = false, studentMail = null, compose = null, notesSummary = null, trainingsSummary = null, bankSummary = null, memoriesSummary = null, communications = null, communicationsSummary = null, absencesPanel = null, studentNotesPanel = null, studentSupport = null, canvasSync = null, gradingScheme = null }) {
  const host = $("#courses-section");
  if (host && !host.querySelector("#courses-grid")) host.innerHTML = listFrame(icon, courseToggle);
  let detailSignature = "";

  /* ---- Per-section counts (GET /api/course-groups/summary) --------------------------------------------------------- */
  let summary = null;
  let summaryAt = 0;
  let summaryLoading = null;
  let summaryGeneration = 0;

  function loadSummary(force = false) {
    if (!force && summary && Date.now() - summaryAt < 15000) return Promise.resolve(summary);
    if (summaryLoading && !force) return summaryLoading;
    const generation = ++summaryGeneration;
    summaryLoading = api("/api/course-groups/summary")
      .then((data) => { if (generation === summaryGeneration) { summary = data; summaryAt = Date.now(); } return data; })
      .catch(() => summary)
      .finally(() => { if (generation === summaryGeneration) summaryLoading = null; });
    return summaryLoading;
  }

  function sectionCounts(groupId, number) {
    return summary?.groups?.[groupId]?.sections?.[String(number)] || null;
  }

  // Counts are painted into placeholders ([data-section-count]) so a late answer never rebuilds the page.
  function applyCounts(root = document) {
    root.querySelectorAll("[data-section-count]").forEach((node) => {
      const counts = sectionCounts(node.dataset.group, node.dataset.section);
      const kind = node.dataset.sectionCount;
      const text = !counts ? "" : kind === "tile" ? sectionDetail(counts) : kind === "card" ? studentsLabel(counts).toLowerCase() : counts.students ? counts.students.toLocaleString() : "no roster";
      if (node.textContent !== text) node.textContent = text;
      node.hidden = !text;
      node.dataset.empty = counts && !counts.students ? "true" : "false";
      const link = node.closest("[data-label-prefix]");
      if (link && counts) link.setAttribute("aria-label", `${link.dataset.labelPrefix}, ${studentsLabel(counts).toLowerCase()}`);
    });
  }

  function refreshCounts(force = false) {
    void loadSummary(force).then(() => applyCounts());
  }

  function isCourseView() {
    return ["all-courses", "current-courses"].includes(state.view);
  }

  function selectedCourseGroup() {
    return state.courseGroups.find(
      (group) =>
        group.id === state.courseId ||
        group.offerings.some((course) => course.id === state.courseId),
    );
  }

  // The Grades row's line last shown per course page, so a page drawn again keeps it while the term list is read again.
  const schemeShown = new Map();
  // A section tab IS its gradebook, where the roster was; the roster is a link away (requirement, 2026-09-28). gradebook.js renders
  // into the tab's [data-gradebook-host]. The course Home has no combined grid: its gradebook address lists the sections.
  const gradebookHref = (group, section = null) => section === null ? `#course/${encodeURIComponent(group.id)}/home/gradebook` : `#course/${encodeURIComponent(group.id)}/section/${section}`;
  const rosterHref = (group, section) => `#course/${encodeURIComponent(group.id)}/section/${section}/roster`;

  function courseHref(
    group,
    section = null,
    from = isCourseView() ? state.view : state.courseBackView,
  ) {
    return `#course/${encodeURIComponent(group.id)}/${section === null ? "home" : `section/${section}`}?from=${from === "current-courses" ? "current-courses" : "all-courses"}`;
  }

  /* ---- Course list ---------------------------------------------------------------------------------------------------- */

  // Storage is shown on a card only when it is not simply working on NVMe, or has a problem.
  function cardStorage(group, single) {
    if (!state.storage.enabled) return "";
    if (single) return single.storage_status === "active" && !single.storage_error ? "" : courseStorageLabel(single);
    const errors = group.offerings.some((course) => course.storage_error);
    const pending = group.offerings.some(storagePending);
    if (!errors && !pending && group.offerings.every((course) => course.storage_status === "active")) return "";
    return `<div class="course-storage ${errors ? "storage-warning" : ""}">${errors ? "Storage needs attention. Open the course for details." : pending ? "Course storage is being updated…" : group.all_current ? "Working copies on NVMe" : group.is_current ? "Current sections on NVMe · past sections on HDD" : "Stored on HDD"}</div>`;
  }

  function sectionChips(group) {
    if (!group.sections.length) return '<p class="course-details">Section details needed</p>';
    return `<div class="chip-row course-section-chips" aria-label="Sections">${group.sections.map((section) => `<a class="chip course-section-chip" href="${courseHref(group, section)}" data-label-prefix="Open ${escape(group.code)} Section ${section}" aria-label="Open ${escape(group.code)} Section ${section}">Section ${section}<b data-section-count="card" data-group="${escape(group.id)}" data-section="${section}" hidden></b></a>`).join("")}</div>`;
  }

  function courseGroupCard(group, { showStatus = true } = {}) {
    const single = group.offerings.length === 1 ? group.offerings[0] : null;
    const action = single
      ? `data-action="edit-course" data-id="${single.id}"`
      : `data-action="group-settings" data-group-id="${group.id}"`;
    const moving = single && storagePending(single);
    const status = showStatus || !group.all_current
      ? `<div class="tool-card-bottom"><span class="badge ${group.is_current ? "ready" : "planned"}"><span class="status-dot ${group.is_current ? "ready" : "planned"}"></span>${groupCurrentLabel(group)}</span></div>`
      : "";
    return `<article class="tool-card course-card" data-course-id="${group.id}">
      <div class="tool-card-top"><span class="tool-icon ${group.is_current ? "sage" : "sand"}">${icon("book")}</span><button class="icon-button course-card-control" ${action} aria-label="${single ? `Edit ${escape(single.name)}` : `Manage ${escape(group.code)} ${escape(courseTerm(group))}`}" title="${moving ? "Settings return when the storage move finishes" : "Course settings"}" ${moving ? "disabled" : ""}>${icon("edit")}</button></div>
      <span class="category">${escape(group.code) || "Course"}</span><h3><a class="course-open" href="${courseHref(group)}" aria-label="Open ${escape(group.code)} · ${escape(courseTerm(group))}">${escape(group.name)}</a></h3>
      ${single?.crn ? `<p class="course-details">CRN ${escape(single.crn)}</p>` : ""}
      ${sectionChips(group)}
      ${cardStorage(group, single)}
      ${moving ? '<p class="course-storage-reason">Storage is moving; settings return when it finishes.</p>' : ""}
      ${status}
    </article>`;
  }

  function termSection(groups, index, showStatus) {
    return `<section class="course-term-group" aria-labelledby="term-heading-${index}"><div class="course-term-heading"><h3 id="term-heading-${index}">${escape(courseTerm(groups[0])) || "Term not set"}</h3><span>${groups.length} ${groups.length === 1 ? "course" : "courses"}</span></div><div class="tools-grid course-term-grid">${groups.map((group) => courseGroupCard(group, { showStatus })).join("")}</div></section>`;
  }

  let knownGroups = null;

  function renderCourses() {
    updateScopeToggle();
    $("#course-storage-summary").innerHTML = storageSummary({ inline: true });
    const count = $("#course-list-count");
    if (!state.loaded) {
      $("#courses-grid").innerHTML = loadingMarkup("Opening your courses…");
      if (count) count.textContent = "";
      return;
    }
    const currentOnly = state.view === "current-courses";
    const query = state.query.toLocaleLowerCase().trim();
    const termOrder = {
      Fall: 6,
      "Summer II": 5,
      "Summer (Full)": 4,
      "Summer I": 3,
      Spring: 2,
      Other: 1,
    };
    const courses = state.courseGroups
      .filter(
        (group) =>
          (!currentOnly || group.is_current) &&
          (!query ||
            group.offerings.some((course) =>
              `${course.name} ${course.code} ${courseTerm(course)} ${course.crn || ""} ${(course.sections || []).join(" ")}`
                .toLocaleLowerCase()
                .includes(query),
            )),
      )
      .sort(
        (a, b) =>
          (b.year || 0) - (a.year || 0) ||
          (termOrder[b.term] || 0) - (termOrder[a.term] || 0) ||
          a.term_other.localeCompare(b.term_other) ||
          a.code.localeCompare(b.code, undefined, { numeric: true }),
      );
    if (count) count.textContent = `${courses.length} ${courses.length === 1 ? "course" : "courses"}`;
    if (!courses.length) {
      $("#courses-grid").innerHTML = query
        ? emptyMarkup({ icon: "search", title: "No courses found.", body: "Try another course code, name, term, or section.", action: { label: "Clear search", data: { action: "clear-course-search" } }, kind: "filtered" })
        : currentOnly && state.courseGroups.length
          ? emptyMarkup({ icon: "book", title: "No current courses yet.", body: "Mark a course as current, or add this term's course.", action: { label: "Browse all courses", data: { action: "all-courses" } } })
          : emptyMarkup({ icon: "book", title: "Your courses start here.", body: "Add your first course to start organizing your teaching.", action: { label: "Add a course", data: { action: "add-course" } } });
      return;
    }
    // All courses only: a course that is new since All courses was last drawn, or was current then, and is now not current.
    const moved = !currentOnly && Boolean(knownGroups) && state.courseGroups.some((group) => !group.is_current && knownGroups.get(group.id) !== false);
    if (!currentOnly) knownGroups = new Map(state.courseGroups.map((group) => [group.id, group.is_current]));
    const byTerm = new Map();
    for (const course of courses) {
      const key = JSON.stringify([
        course.term,
        course.term === "Other" ? course.term_other : "",
        course.year,
      ]);
      if (!byTerm.has(key)) byTerm.set(key, []);
      byTerm.get(key).push(course);
    }
    const terms = [...byTerm.values()];
    // In All courses, terms with no current section fold away (open while searching).
    const current = currentOnly ? terms : terms.filter((groups) => groups.some((group) => group.is_current));
    const earlier = currentOnly ? [] : terms.filter((groups) => !groups.some((group) => group.is_current));
    const showStatus = !currentOnly;
    let markup = current.map((groups, index) => termSection(groups, index, showStatus)).join("");
    if (earlier.length) {
      const total = earlier.reduce((sum, groups) => sum + groups.length, 0);
      const body = earlier.map((groups, index) => termSection(groups, current.length + index, showStatus)).join("");
      // Open while searching (a match must be visible), and when a course was just added to it or moved into it (so the
      // instructor sees where it went); otherwise the browser remembers whether it was left open.
      if (moved) {
        try { globalThis.localStorage?.setItem("faculty:disclosure:courses:not-current", "1"); } catch { /* a convenience only */ }
      }
      markup += disclosureMarkup({ label: "Terms not current", hint: `${total} ${total === 1 ? "course" : "courses"}`, body, key: query ? "" : "courses:not-current", open: Boolean(query) || moved, className: "course-other-terms" });
    }
    $("#courses-grid").innerHTML = markup;
    applyCounts($("#courses-grid"));
    refreshCounts();
  }

  /* ---- Course page: course home and section tabs ---------------------------------------------------------------------- */

  function offeringDetails(course, section = null) {
    const sectionLabel =
      section === null
        ? `${course.sections.length === 1 ? "Section" : "Sections"} ${course.sections.join(", ") || "not set"}`
        : `Section ${section}`;
    return `<article class="offering-details"><div class="offering-heading"><div><h3>${escape(sectionLabel)}</h3><p>${escape(course.name)} · ${course.crn ? `CRN ${escape(course.crn)}` : "No CRN"} · ${course.is_current ? "Current" : "Not current"}</p></div><button class="button" data-action="edit-course" data-id="${course.id}" aria-label="Edit ${escape(course.name)}" ${storagePending(course) ? "disabled" : ""}>${icon("edit")}Edit details</button></div>${storagePending(course) ? '<p class="course-storage-reason">Storage is moving; settings return when it finishes.</p>' : ""}${courseStorageLabel(course)}</article>`;
  }

  // The fields that change on every nightly backup or verification: they must not rebuild the page (and its roster).
  const VOLATILE = new Set(["storage_snapshot", "storage_verified_at"]);
  const stable = (value) => JSON.stringify(value, (key, item) => (VOLATILE.has(key) ? undefined : item));

  function workFrame(group, selected, sub = "gradebook") {
    // A section tab shows its gradebook; its roster is this link (and the roster page links back to the gradebook).
    const gradebook = selected === null
      ? ""
      : sub === "roster"
        ? `<a class="link-arrow" href="${gradebookHref(group, selected)}" aria-label="Section ${selected} gradebook">Gradebook</a>`
        : `<a class="link-arrow" href="${rosterHref(group, selected)}" aria-label="Section ${selected} roster" data-course-roster-link>Roster</a>`;
    // The term's weekly schedule (SCHEDULING): class meetings, office hours and meeting hours, set once a term.
    const scheduleHome = selected === null ? `<li class="course-work-row" data-schedule-row><span class="course-work-kind">Schedule</span><span class="course-work-links"><a class="link-arrow" href="#course/${encodeURIComponent(group.id)}/home/schedule" aria-label="Term schedule: class meetings, office hours and meeting hours">Term schedule</a></span></li>` : "";
    // The term's grading scheme (GRADEBOOK GRADING SCHEME, requirement, 2026-10-06): one letter scale for every section, edited here.
    const schemeHome = selected === null && gradingScheme ? `<li class="course-work-row" data-grading-scheme-row><span class="course-work-kind">Grades</span><span class="course-work-links"><button type="button" class="text-button" data-grading-scheme aria-label="Grading scheme: the letters every section of this term gives">Grading scheme</button><small class="course-scheme-status" data-grading-scheme-status>${escape(schemeShown.get(group.id) || "")}</small></span></li>` : "";
    // A section's Class schedule (SCHEDULING "CLASS SCHEDULE"): its class days, what each covers, and the work given or due.
    const scheduleSection = selected === null ? "" : `<li class="course-work-row" data-class-schedule-row><span class="course-work-kind">Schedule</span><span class="course-work-links"><a class="link-arrow" href="#course/${encodeURIComponent(group.id)}/section/${selected}/schedule" aria-label="Section ${selected} class schedule: class days, topics, and what is given or due">Class schedule</a></span></li>`;
    // The term's Canvas content has its own row on the course Home, with where it stands (canvas-hub.js has every course's).
    const canvasHome = selected === null ? `<li class="course-work-row" data-canvas-home-row><span class="course-work-kind">Canvas</span><span class="course-work-links"><a class="link-arrow" href="#course/${encodeURIComponent(group.id)}/home/canvas" aria-label="Canvas modules, pages and assignments of every section">Modules, pages & assignments</a><small class="course-canvas-status" data-canvas-home-status></small></span></li>` : "";
    const documents = selected === null ? "" : `<a class="link-arrow" href="#documents?definition=${encodeURIComponent(group.definition_id || "")}" aria-label="Assignments">Assignments</a>`;
    // On a section tab Course settings is a quiet control here, not the header's primary (the tab's goal is its roster);
    // it opens the same settings as course home's header, with the same storage guard.
    const single = group.offerings.length === 1 ? group.offerings[0] : null;
    const moving = Boolean(single && storagePending(single));
    const settings = selected === null ? "" : `<button type="button" class="text-button course-work-settings" ${single ? `data-action="edit-course" data-id="${escape(single.id)}"` : `data-action="group-settings" data-group-id="${escape(group.id)}"`}${moving ? ' disabled title="Settings return when the storage move finishes"' : ""}>${icon("edit")}Course settings</button>`;
    // The course Home's links are in its right-hand column, so its Records row would be empty: it is drawn only when it holds one.
    const records = `${gradebook}${documents}${settings}`;
    // A section tab's goal is its gradebook: its exams, seating, grading and cards fold into one closed line with their counts
    // (loadWork draws it; the viewer's choice is remembered), so the grid starts higher on the page. The roster tab and the
    // course Home keep the list open.
    const folded = selected !== null && sub === "gradebook";
    return `<div class="course-work" data-course-work data-compact="${selected !== null}"><h4 class="course-work-title">${selected === null ? "This term’s work" : "This section’s work"}</h4><ul class="course-work-list">${records ? `<li class="course-work-row" data-course-records><span class="course-work-kind">Records</span><span class="course-work-links">${records}</span></li>` : ""}${schemeHome}${canvasHome}${scheduleHome}${scheduleSection}${selected === null ? "" : '<li class="course-work-row" data-ta-row hidden><span class="course-work-kind">TAs</span><span class="course-work-links" data-ta-list></span></li><li class="course-work-row" data-gradescope-row hidden><span class="course-work-kind">Gradescope</span><span class="course-work-links" data-gradescope-link></span></li><li class="course-work-row" data-canvas-row hidden><span class="course-work-kind">Canvas</span><span class="course-work-links" data-canvas-link></span></li>'}</ul><div data-course-work-rows${folded ? " data-folded" : ""}>${loadingMarkup(selected === null ? "Finding this term’s work…" : "Finding this section’s work…", folded ? 1 : 2)}</div></div>`;
  }

  function sectionTiles(group) {
    if (!group.sections.length) return '<p class="field-hint">Add section numbers in the course settings.</p>';
    return group.sections.map((section) => `<a class="section-shortcut" href="${courseHref(group, section)}" aria-label="Open Section ${section}" aria-describedby="section-tile-${section}"><span class="section-shortcut-name">${icon("users")}Section ${section}</span><small id="section-tile-${section}" data-section-count="tile" data-group="${escape(group.id)}" data-section="${section}" hidden></small>${group.current_sections.includes(section) ? "" : "<small>Not current</small>"}${icon("arrow-right")}</a>`).join("");
  }

  function homePanel(group, relatedTerms, currentTerms) {
    const settingsOpen = group.offerings.length > 1 || group.offerings.some((course) => course.storage_error || storagePending(course));
    // The overview of current sections across terms. When the selected term holds every current section, its section tiles
    // below are that overview, so the strip would only repeat them: it is shown when another term has current sections.
    const elsewhere = currentTerms.some((term) => term.id !== group.id);
    const teachingNow = elsewhere
      ? `<section id="course-current-sections" class="course-now" aria-labelledby="current-sections-title"><h3 id="current-sections-title">Teaching now</h3><div class="current-terms">${currentTerms.map((term) => `<div class="current-term"><h4>${escape(courseTerm(term))}</h4><div class="current-section-links">${term.current_sections.map((section) => `<a href="${courseHref(term, section)}" aria-label="Open Section ${section} in ${escape(courseTerm(term))}">Section ${section}<b data-section-count="chip" data-group="${escape(term.id)}" data-section="${section}" hidden></b></a>`).join("")}</div></div>`).join("")}</div></section>`
      : `<section id="course-current-sections" class="course-now" hidden></section>`;
    const term = `<section id="course-term-overview" class="course-panel course-term-overview" aria-labelledby="selected-term-title"><div class="course-panel-head"><h3 id="selected-term-title">${escape(courseTerm(group)) || "Term not set"}</h3><span class="chip" data-tone="${group.is_current ? "success" : "neutral"}">${groupCurrentLabel(group)}</span></div>${workFrame(group, null)}<div class="course-sections-head"><h4>Sections</h4>${group.sections.length > 1 ? `<button type="button" class="button" data-course-import-rosters>${icon("plus")}Import rosters</button>` : ""}</div><div class="section-shortcuts">${sectionTiles(group)}</div><div data-inbox-resources="term" hidden></div></section>`;
    const shared = `<section id="course-shared" class="course-panel course-shared" aria-labelledby="shared-course-title"><div class="course-panel-head"><h3 id="shared-course-title">${escape(group.code)} · every term</h3></div><div class="course-terms"><h4>Teaching terms</h4><div class="course-term-links">${relatedTerms.map((item) => `<a href="${courseHref(item)}" ${item.id === group.id ? 'aria-current="page"' : ""}>${escape(courseTerm(item)) || "Term not set"}${icon("arrow-right")}</a>`).join("")}</div><button type="button" class="text-button course-next-term" data-course-next-term>${icon("plus")}Start next term</button></div><p class="course-shared-links"><a class="link-arrow" href="#documents?definition=${encodeURIComponent(group.definition_id || "")}" aria-label="Assignments">Assignments</a></p>${group.definition_id ? `<p class="course-shared-links course-notes-link"><a class="link-arrow" href="#course/${encodeURIComponent(group.id)}/home/notes" aria-label="Lecture notes, split into their sections">Lecture notes</a><small class="course-notes-status" data-notes-status></small></p><p class="course-shared-links course-trainings-link"><a class="link-arrow" href="#recordings?course=${encodeURIComponent(group.definition_id)}" aria-label="Training recordings, kept for every term">Training recordings</a><small class="course-trainings-status" data-trainings-status></small></p><p class="course-shared-links course-bank-link"><a class="link-arrow" href="#course/${encodeURIComponent(group.id)}/home/questions" aria-label="Question bank, by chapter, section and topic">Question bank</a><small class="course-bank-status" data-bank-status></small></p><p class="course-shared-links course-memories-link"><a class="link-arrow" href="#memory?course=${encodeURIComponent(group.definition_id)}" aria-label="Rubric memories, what your rubrics are drafted and checked by (opens Memory)">Rubric memories</a><small class="course-memories-status" data-memories-status></small></p>` : ""}<details class="disclosure course-documents" data-disclosure-key="course:documents"><summary><span>Course documents</span><span class="disclosure-hint" data-course-documents-count></span></summary><div class="disclosure-body"><div data-inbox-resources="course">${loadingMarkup("Loading course documents…", 2)}</div></div></details></section>`;
    const settings = `<details id="course-settings" class="disclosure course-settings-section" tabindex="-1" ${settingsOpen ? "open" : ""}><summary><span>Course settings</span><span class="disclosure-hint">${group.offerings.length} saved ${group.offerings.length === 1 ? "entry" : "entries"}</span></summary><div class="disclosure-body"><div class="offering-list">${group.offerings.map((course) => offeringDetails(course)).join("")}</div></div></details>`;
    return `${teachingNow}<div class="course-home-grid">${term}${shared}</div>${settings}`;
  }

  function renderCourseDetail() {
    const node = $("#course-detail");
    const group = selectedCourseGroup();
    const relatedTerms = group
      ? state.courseGroups.filter((item) =>
          group.definition_id
            ? item.definition_id === group.definition_id
            : group.code
              ? item.code === group.code
              : item.id === group.id,
        )
      : [];
    const currentTerms = relatedTerms.filter(
      (item) => item.current_sections.length,
    );
    const selected = state.courseSection;
    const sub = ["gradebook", "roster", "canvas", "notes", "questions", "rubric-memories", "schedule"].includes(state.courseSub) ? state.courseSub : "overview";
    // The gradebook, the lecture notes, the question bank and the rubric memories name themselves in the header (its term, section and
    // actions); the course Home and a roster are named here.
    if (group && state.loaded && sub === "canvas" && selected !== null) {
      // One section's Canvas course edited directly (canvas-content.js section mode). No lead under the band: the work starts
      // there (DESIGN-LANGUAGE §1.1), and the chosen tab and the page's bar say whose Canvas course this is.
      header?.set({
        route: location.hash,
        title: `Canvas · Section ${selected}`,
        context: [group.code, courseTerm(group) || "Term not set"].filter(Boolean).join(" · "),
        subtitle: "",
        crumbs: [
          { label: "Home", href: "#overview" },
          { label: "Courses", href: `#${state.courseBackView}` },
          { label: `${group.code} · ${courseTerm(group)}`, href: courseHref(group) },
          { label: `Section ${selected}`, href: courseHref(group, selected) },
          { label: "Canvas" },
        ],
        action: null,
      });
    } else if (group && state.loaded && sub === "canvas") {
      header?.set({
        route: location.hash,
        title: "Canvas content",
        context: [group.code, courseTerm(group) || "Term not set"].filter(Boolean).join(" · "),
        subtitle: "",
        crumbs: [
          { label: "Home", href: "#overview" },
          { label: "Courses", href: `#${state.courseBackView}` },
          { label: `${group.code} · ${courseTerm(group)}`, href: courseHref(group) },
          { label: "Canvas content" },
        ],
        action: null,
      });
    } else if (group && state.loaded && sub === "roster" && selected !== null && group.sections.includes(selected)) {
      // A section's roster names itself like the section's gradebook does ('MATH 140 · Section 507', the trail ending in Roster).
      header?.set({
        route: location.hash,
        title: `${group.code || "Course"} · Section ${selected}`,
        context: [courseTerm(group) || "Term not set", "Roster"].join(" · "),
        subtitle: group.name,
        crumbs: [
          { label: "Home", href: "#overview" },
          { label: "Courses", href: `#${state.courseBackView}` },
          { label: `${group.code} · ${courseTerm(group)}`, href: courseHref(group) },
          { label: `Section ${selected}`, href: courseHref(group, selected) },
          { label: "Roster" },
        ],
        tab: `Roster · ${group.code} · ${courseTerm(group)} · Section ${selected}`,
        action: null,
      });
    } else if (group && state.loaded && sub !== "gradebook" && sub !== "notes" && sub !== "questions" && sub !== "rubric-memories" && sub !== "schedule") {
      header?.set({
        route: location.hash,
        title: group.code || "Course",
        context: [courseTerm(group) || "Term not set", selected !== null && group.sections.includes(selected) ? `Section ${selected}` : ""].filter(Boolean).join(" · "),
        subtitle: group.name,
        crumbs: [
          { label: "Home", href: "#overview" },
          { label: "Courses", href: `#${state.courseBackView}` },
          { label: `${group.code} · ${courseTerm(group)}`, href: selected === null ? undefined : courseHref(group) },
          ...(selected === null ? [] : [{ label: `Section ${selected}` }]),
        ],
        // Course settings is setup, never a page's main action (DESIGN-LANGUAGE §1.4): a secondary in course home's header, and a
        // quiet control in a section tab's work list (a section tab's work is its roster).
        ...(selected === null ? { secondary: [{
          label: "Course settings", icon: "edit", action: group.offerings.length === 1 ? "edit-course" : "group-settings",
          data: { id: group.offerings[0]?.id || "", groupId: group.id },
          disabled: Boolean(group.offerings.length === 1 && storagePending(group.offerings[0])),
        }] } : { action: null }),
      });
    }
    const signature = stable([
      state.loaded,
      group,
      relatedTerms,
      state.courseSection,
      sub,
      state.courseSetup,
      state.courseBackView,
      state.storage.enabled,
    ]);
    if (signature === detailSignature && node.hasChildNodes()) {
      refreshCounts();
      return;
    }
    detailSignature = signature;
    const focusedTab =
      document.activeElement?.getAttribute("role") === "tab"
        ? document.activeElement.id
        : null;
    // No "Back to all courses" link (DESIGN-LANGUAGE §1.8, one navigation layer; requirement, 2026-10-04): the trail's Courses crumb goes back to the collection that opened the course.
    if (!state.loaded) {
      node.innerHTML = loadingMarkup("Opening your course…");
      return;
    }
    if (!group) {
      node.innerHTML = `${emptyMarkup({ icon: "book", title: "Course not found", body: "This course may have been removed or moved to a different code or term.", action: { label: "Browse courses", href: `#${state.courseBackView}` }, heading: "h2" })}`;
      return;
    }
    const missingSection =
      selected !== null && !group.sections.includes(selected);
    // On the Canvas pages the tabs say whose Canvas course is open: every section's (Faculty's one copy) or one section's own.
    const tabs = [
      { value: null, label: sub === "canvas" ? "All sections" : "Home", id: "course-tab-home" },
      ...group.sections.map((section) => ({
        value: section,
        label: `Section ${section}`,
        id: `course-tab-section-${section}`,
      })),
    ];
    let panel;
    if (missingSection) {
      panel = emptyMarkup({ icon: "users", title: "Section not found", body: "This section is no longer part of this course. Choose another section or return to the course home.", action: { label: "Course home", href: courseHref(group) }, heading: "h2" });
    } else if (sub === "canvas") {
      // The term's Canvas modules and pages (canvas-content.js), one copy pushed to every section's Canvas course.
      panel = `<section class="course-canvas-content" data-canvas-content-host data-host-key="${escape(`${group.id}:${selected ?? "home"}`)}" aria-label="${selected === null ? "Canvas modules and pages" : `Section ${selected}’s Canvas course`}"></section>`;
    } else if (sub === "notes" && selected === null) {
      // The course's lecture notes (course-notes.js): one set for every term and section, each section its own PDF.
      panel = `<section class="course-notes" data-course-notes-host data-host-key="${escape(group.definition_id || group.id)}" aria-label="Lecture notes"></section>`;
    } else if (sub === "schedule" && selected === null) {
      // The term's weekly Term schedule (term-schedule.js, SCHEDULING): class meetings, office hours and meeting hours.
      panel = `<section class="term-schedule-host" data-term-schedule-host data-host-key="${escape(group.id)}" aria-label="Term schedule"></section>`;
    } else if (sub === "schedule") {
      // A section's Class schedule (class-schedule.js): the page alone, as the Term schedule is on the course Home.
      panel = `<section class="class-schedule-host" data-class-schedule-host data-host-key="${escape(`${group.id}:${selected}`)}" aria-label="Class schedule, Section ${selected}"></section>`;
    } else if (sub === "questions" && selected === null) {
      // The course's question bank (question-bank.js): one bank for every term and section, tagged by the notes' outline.
      panel = `<section class="question-bank" data-question-bank-host data-host-key="${escape(group.definition_id || group.id)}" aria-label="Question bank"></section>`;
    } else if (sub === "rubric-memories" && selected === null) {
      // The course's rubric memories are on the Memory page (ASSISTANT-MEMORY "ONE PAGE"); the shell sends an old link there.
      panel = `<p class="course-shared-links"><a class="link-arrow" href="#memory${group.definition_id ? `?course=${encodeURIComponent(group.definition_id)}` : ""}">Rubric memories are on the Memory page</a></p>`;
    } else if (sub === "gradebook") {
      // The section's gradebook where the roster was (the setup page alone), then the section's work under it, so the grid starts on
      // the first screen (DESIGN-LANGUAGE §1.1, Ledger 2026-10-04); the course Home: which section.
      const host = `<section class="course-gradebook" data-gradebook-host data-host-key="${escape(`${group.id}:${selected ?? "home"}`)}" aria-label="${selected === null ? "Gradebook sections" : `Gradebook, Section ${selected}`}"></section>`;
      panel = selected === null || state.courseSetup ? host : `${host}${workFrame(group, selected, sub)}`;
    } else if (selected === null) {
      panel = homePanel(group, relatedTerms, currentTerms);
    } else {
      panel = `${workFrame(group, selected, sub)}<section id="section-roster" data-group-id="${escape(group.id)}" data-section="${selected}" aria-label="Section roster">${loadingMarkup("Loading roster…")}</section><section class="course-panel" data-inbox-resources="section" hidden></section>`;
    }
    // The roster keeps its search, sort, filter and scroll when the page around it is rebuilt for the same section; so does the
    // gradebook grid (and any cell being edited) of the same tab.
    const keptRoster = node.querySelector("#section-roster");
    const keptHosts = KEPT_HOSTS.map((selector) => node.querySelector(selector));
    // On a schedule the tabs lead to each tab's schedule: the term's weekly one on Home, each section's own beside it. On the
    // Canvas pages they stay in Canvas too: the copy every section shares, and each section's own Canvas course.
    const tabHref = (value) => (sub === "schedule" || sub === "canvas" ? `#course/${encodeURIComponent(group.id)}/${value === null ? "home" : `section/${value}`}/${sub}` : courseHref(group, value));
    const activeId =
      selected === null ? "course-tab-home" : `course-tab-section-${selected}`;
    node.innerHTML = `<div class="course-tabs" role="tablist" aria-label="Course sections">${tabs.map((tab) => `<a id="${tab.id}" role="tab" aria-selected="${!missingSection && selected === tab.value}" aria-controls="course-panel" tabindex="${selected === tab.value || (missingSection && tab.value === null) ? "0" : "-1"}" href="${tabHref(tab.value)}">${tab.value === null && sub !== "canvas" ? icon("home") : ""}${tab.label}</a>`).join("")}</div><div id="course-panel" role="tabpanel" ${missingSection ? 'aria-label="Unavailable section"' : `aria-labelledby="${activeId}"`} tabindex="0">${panel}</div>`;
    const placeholder = node.querySelector("#section-roster");
    if (keptRoster && placeholder && keptRoster.dataset.groupId === placeholder.dataset.groupId && keptRoster.dataset.section === placeholder.dataset.section) {
      placeholder.replaceWith(keptRoster);
    }
    KEPT_HOSTS.forEach((selector, index) => {
      const kept = keptHosts[index], slot = node.querySelector(selector);
      if (kept && slot && kept.dataset.hostKey === slot.dataset.hostKey) slot.replaceWith(kept);
    });
    if (focusedTab)
      document.getElementById(focusedTab)?.focus({ preventScroll: true });
    applyCounts(node);
    refreshCounts();
    void loadRosterPanel();
    if (!missingSection && selected === null && sub === "overview") void notesSummary?.(group, node.querySelector("[data-notes-status]"));
    // COURSE-NOTES "TRAINING RECORDINGS": the recordings filed as this course's training, kept for every term (recordings.js).
    if (!missingSection && selected === null && sub === "overview") void trainingsSummary?.(group, node.querySelector("[data-trainings-status]"));
    if (!missingSection && selected === null && sub === "overview") void bankSummary?.(group, node.querySelector("[data-bank-status]"));
    if (!missingSection && selected === null && sub === "overview") void memoriesSummary?.(group, node.querySelector("[data-memories-status]"));
    if (!missingSection && (selected === null ? sub === "overview" : !state.courseSetup && sub !== "canvas" && sub !== "schedule")) {
      void loadWork(group, selected);
      void Promise.resolve(courseResources(group, selected)).then(() => {
        const documents = node.querySelector("[data-course-documents-count]");
        if (documents) documents.textContent = String(node.querySelectorAll('[data-inbox-resources="course"] .inbox-course-file').length);
      });
    }
  }

  /* ---- The work hub: this term's (or section's) exams, seating charts, grading and response cards -------------------- */
  const workCache = new Map();
  let workRequest = 0;

  async function workSources(termId) {
    const cached = workCache.get(termId);
    if (cached && Date.now() - cached.at < 60000) return cached.data;
    const [grading, exams, seating, cards] = await Promise.allSettled([
      api("/api/grading?view=navigation&health=0"),
      api("/api/exams?summary=0"),
      api("/api/state?include=seating"),
      api(`/api/response-cards/issues?term_id=${encodeURIComponent(termId)}`),
    ]);
    const value = (result, key) => (result.status === "fulfilled" && Array.isArray(result.value?.[key]) ? result.value[key] : null);
    const data = {
      grading: value(grading, "assignments"),
      exams: value(exams, "exams"),
      seating: value(seating, "seating_charts"),
      cards: value(cards, "issues"),
    };
    data.failed = [grading, exams, seating, cards].some((result) => result.status !== "fulfilled");
    workCache.set(termId, { at: Date.now(), data });
    return data;
  }

  async function loadWork(group, selected) {
    const request = ++workRequest;
    const counts = await loadSummary();
    const target = document.querySelector("[data-course-work-rows]");
    if (request !== workRequest || !target?.isConnected) return;
    const termId = counts?.groups?.[group.id]?.term_id;
    if (!termId) {
      target.innerHTML = '<p class="course-work-empty">Links to this term’s exams, seating and grading appear once the course has a code, term and year.</p>';
      return;
    }
    const sectionId = selected === null ? null : counts.groups[group.id].sections?.[String(selected)]?.section_id || "";
    if (sectionId) { void loadTAs(sectionId); void loadGradescope(sectionId); void loadCanvas(sectionId); }
    if (selected === null) { void loadCanvasHome(group); void loadGradingScheme(group); }
    let sources;
    try {
      sources = await workSources(termId);
    } catch {
      sources = { failed: true };
    }
    if (request !== workRequest || !target.isConnected) return;
    const sectionNumbers = Object.fromEntries(Object.entries(counts.groups[group.id].sections || {}).map(([number, section]) => [section.section_id, Number(number)]));
    const rows = workRows({ termId, sectionId, sectionNumber: selected, sectionNumbers, ...sources,
      definitionId: group.definition_id || null, currentTerm: group.is_current !== false });
    // A certified assessment's charts and card sets are archived (CERTIFIED-ARCHIVE): counted, and found on their own page.
    const archivedLink = (row) => row.archived && row.more
      ? `<a class="course-work-more" href="${escape(row.more)}" title="${escape(`Grades certified: listed under Archived on the ${row.kind} page`)}">${row.archived.toLocaleString()} archived</a>` : "";
    const list = `<ul class="course-work-list">${rows.map((row) => `<li class="course-work-row"><span class="course-work-kind">${escape(row.kind)}</span><span class="course-work-links">${row.items.slice(0, 6).map((item) => `<a href="${escape(item.href)}">${escape(item.label)}${item.note ? ` <small>· ${escape(item.note)}</small>` : ""}</a>`).join("")}${row.items.length > 6 && row.more ? `<a class="course-work-more" href="${escape(row.more)}">${(row.items.length - 6).toLocaleString()} more</a>` : ""}${archivedLink(row)}</span></li>`).join("")}</ul>`;
    const markup = !rows.length
      ? `<p class="course-work-empty">No exams, seating charts, grading or response cards ${selected === null ? "for this term" : "name this section"} yet.</p>`
      : target.hasAttribute("data-folded") ? disclosureMarkup({ label: "Assessments", hint: workCounts(rows), body: list, key: "course:section-work" })
        : list;
    target.innerHTML = markup + (sources.failed ? '<p class="course-work-empty">Some of this term’s work could not be read. It will be tried again when you return.</p>' : "");
  }

  /* ---- The section's TAs (teaching_assistants/service.py) ------------------------------------------------------------ */
  // Their contact details belong to the section (requirement, 2026-09-28). A name opens
  // an email in the instructor's own mail program; Faculty sends nothing. Copy from another section only fills the form.
  const taRoute = (sectionId) => `/api/teaching-assistants/sections/${encodeURIComponent(sectionId)}`;
  let taRequest = 0;

  async function loadTAs(sectionId) {
    const row = document.querySelector("[data-ta-row]");
    const request = ++taRequest;
    let view;
    try {
      view = await api(taRoute(sectionId));
    } catch {
      return;
    }
    const slot = row?.querySelector("[data-ta-list]");
    if (request !== taRequest || !slot?.isConnected) return;
    row.hidden = false;
    const subject = taSubject(view);
    const emails = view.contacts.map((ta) => ta.email).filter(Boolean);
    const people = view.contacts.map((ta) => {
      const details = [ta.email, ta.phone, ta.note].filter(Boolean).join(" · ");
      const name = ta.email
        ? `<a href="${escape(taMailto([ta.email], subject))}" aria-label="Email ${escape(ta.name)}">${escape(ta.name)}</a>`
        : `<span class="course-ta-name">${escape(ta.name)}</span>`;
      return `<span class="course-ta">${name}${details ? `<small>${escape(details)}</small>` : ""}</span>`;
    }).join("");
    const all = emails.length > 1 ? `<a href="${escape(taMailto(emails, subject))}">Email all ${emails.length} TAs</a>` : "";
    const copy = emails.length ? `<button type="button" class="text-button" data-ta-copy>Copy email${emails.length === 1 ? "" : "s"}</button>` : "";
    slot.innerHTML = view.contacts.length
      ? `${people}${all}${copy}<button type="button" class="text-button" data-ta-edit>Edit TAs…</button>`
      : '<span class="course-gradescope-course">No TAs added</span><button type="button" class="text-button" data-ta-edit>Add TAs…</button>';
    slot.querySelector("[data-ta-edit]").onclick = () => taDialog(view);
    const copyButton = slot.querySelector("[data-ta-copy]");
    if (copyButton) copyButton.onclick = async () => {
      const copied = await copyText(emails.join(", "));
      toast(copied ? `Copied ${emails.length} email address${emails.length === 1 ? "" : "es"}.` : "Your browser did not allow copying. The addresses are shown beside each TA.", !copied);
    };
  }

  function taFields(ta, index) {
    const value = (key) => escape(ta[key] || "");
    return `<fieldset class="ta-edit-row" data-ta-edit-row><legend>TA ${index + 1}</legend><div class="field-row"><label class="field">Name<input name="name" maxlength="120" value="${value("name")}" autocomplete="off"></label><label class="field">Email<input name="email" type="email" maxlength="254" value="${value("email")}" inputmode="email" spellcheck="false" autocapitalize="off" autocomplete="off"></label></div><div class="field-row"><label class="field">Phone<span class="field-optional">optional</span><input name="phone" type="tel" maxlength="40" value="${value("phone")}" autocomplete="off"></label><label class="field">Note<span class="field-optional">optional</span><input name="note" maxlength="300" value="${value("note")}" placeholder="Office hours, recitation, grading…" autocomplete="off"></label></div><button type="button" class="text-button danger" data-ta-remove aria-label="Remove TA ${index + 1}">${icon("trash")}Remove</button></fieldset>`;
  }

  function taDialog(view) {
    const requestId = crypto.randomUUID();
    const copyFrom = view.others.length
      ? `<p class="ta-copy-from"><span>Copy from</span>${view.others.map((other) => `<button type="button" class="text-button" data-ta-copy-from="${escape(other.section_id)}">Section ${escape(other.section)} <small>· ${other.contacts.length} TA${other.contacts.length === 1 ? "" : "s"}</small></button>`).join("")}</p>`
      : "";
    showDialog(`<form id="ta-form" novalidate>${dialogHeader("TAs", `Section ${escape(view.section)} · ${escape(view.term_label)}`)}<div class="dialog-body"><p>This section’s TAs. On the section page each name opens an email to them in your mail app.</p>${copyFrom}<p class="field-hint" data-ta-copied role="status" hidden></p><div class="ta-edit-list" data-ta-edit-list></div><button type="button" class="text-button" data-ta-add>${icon("plus")}Add another TA</button><p id="form-error" class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Save</button></footer></form>`);
    const form = $("#ta-form");
    const list = form.querySelector("[data-ta-edit-list]");
    const read = () => [...list.querySelectorAll("[data-ta-edit-row]")].map((row) => Object.fromEntries(["name", "email", "phone", "note"].map((key) => [key, row.querySelector(`[name="${key}"]`).value])));
    const draw = (contacts) => {
      list.innerHTML = (contacts.length ? contacts : [{}]).map(taFields).join("");
      list.querySelectorAll("[data-ta-remove]").forEach((button, index) => {
        button.onclick = () => draw(read().filter((_, other) => other !== index));
      });
    };
    draw(view.contacts);
    form.querySelector("[data-ta-add]").onclick = () => {
      draw([...read(), {}]);
      list.querySelector("[data-ta-edit-row]:last-child [name='name']")?.focus();
    };
    form.querySelectorAll("[data-ta-copy-from]").forEach((button) => {
      button.onclick = () => {
        const other = view.others.find((item) => item.section_id === button.dataset.taCopyFrom);
        const merged = mergeTAs(read(), other?.contacts || []);
        draw(merged.contacts);
        const note = form.querySelector("[data-ta-copied]");
        note.textContent = merged.added
          ? `Added ${merged.added} TA${merged.added === 1 ? "" : "s"} from Section ${other.section}. Save to keep them here.`
          : `Everyone listed for Section ${other.section} is already here.`;
        note.hidden = false;
      };
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      await saveForm(
        form,
        () => api(taRoute(view.section_id), "POST", { contacts: read(), base_revision: view.revision, request_id: requestId }),
        "TA contacts saved.",
        () => void loadTAs(view.section_id),
      );
    });
  }

  /* ---- The section's Gradescope course ------------------------------------------------------------------------------ */
  // Set once per section (requirement, 2026-09-27): grading's Gradescope dialogs then show it as a fact instead of asking.
  // Reading the link does not contact Gradescope; only Change reads the course list.
  async function loadGradescope(sectionId) {
    const row = document.querySelector("[data-gradescope-row]");
    let link;
    try {
      link = await api(`/api/grading/gradescope/sections/${encodeURIComponent(sectionId)}`);
    } catch {
      return;
    }
    const slot = row?.querySelector("[data-gradescope-link]");
    if (!slot?.isConnected || !link?.configured) return;
    row.hidden = false;
    const linked = link.linked;
    slot.innerHTML = `${linked ? `<span class="course-gradescope-course">${escape(linked.course_title)}</span>` : '<span class="course-gradescope-course">No Gradescope course linked</span>'}<button type="button" class="text-button" data-gradescope-change>${linked ? "Change…" : "Link a Gradescope course…"}</button>`;
    slot.querySelector("[data-gradescope-change]").onclick = () => gradescopeDialog(link);
  }

  function gradescopeDialog(link) {
    const base = link.linked?.course_id || "";
    showDialog(`<form id="gradescope-link-form">${dialogHeader("Gradescope course", `Section ${escape(link.section)} · ${escape(link.term_label)}`)}<div class="dialog-body"><p>Grades, papers and bubble sheets of this section go to this Gradescope course. Each assignment still chooses its own Gradescope assignment there.</p><label class="field">Gradescope course<select name="course_id" required disabled><option value="">Reading your Gradescope courses…</option></select></label><p class="field-hint" data-gradescope-proposed hidden>Proposed from the section number and term. Check it before saving.</p><p id="form-error" class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary" disabled>Save</button></footer></form>`);
    const form = $("#gradescope-link-form");
    const active = dialogSession(form, $("#dialog"));
    const select = form.elements.course_id, submit = form.querySelector('[type="submit"]');
    void api(`/api/grading/gradescope/sections/${encodeURIComponent(link.section_id)}/courses`, "GET", undefined, {}, 60000).then((result) => {
      if (!active()) return;
      const chosen = base || result.proposed || "";
      const label = (c) => `${[c.short, c.name].filter(Boolean).join(" · ")}${c.term ? ` (${c.term})` : ""}`;
      select.innerHTML = `<option value="" ${chosen ? "" : "selected"} disabled>Choose a Gradescope course…</option>${(result.courses || []).map((c) => `<option value="${escape(c.id)}" ${String(c.id) === chosen ? "selected" : ""}>${escape(label(c))}</option>`).join("")}`;
      form.querySelector("[data-gradescope-proposed]").hidden = Boolean(base) || !result.proposed;
      select.disabled = false;
      submit.disabled = false;
      select.focus();
    }).catch((error) => {
      if (!active()) return;
      select.innerHTML = '<option value="">Gradescope could not be read</option>';
      showFormError(form, error);
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!select.value) return;
      await saveForm(
        form,
        () => api(`/api/grading/gradescope/sections/${encodeURIComponent(link.section_id)}`, "POST", { course_id: select.value, base }, {}, 60000),
        "Gradescope course saved.",
        () => void loadGradescope(link.section_id),
      );
    });
  }

  /* ---- The section's Canvas course (canvas/service.py) ---------------------------------------------------------------- */
  // Linked once per section like Gradescope. Canvas is read with the session the sign-in window keeps on the server; when Canvas
  // wants the instructor again (401), the sign-in window opens and the step is tried once more after it.
  const canvasRoute = (sectionId, rest = "") => `/api/canvas/sections/${encodeURIComponent(sectionId)}${rest}`;
  const signInToCanvas = () => openCanvasSignIn({ api, showDialog, dialogHeader, escape });

  async function loadCanvas(sectionId) {
    const row = document.querySelector("[data-canvas-row]");
    let link;
    try {
      link = await api(canvasRoute(sectionId));
    } catch {
      return;
    }
    const slot = row?.querySelector("[data-canvas-link]");
    if (!slot?.isConnected || !link?.configured) return;
    row.hidden = false;
    const linked = link.linked;
    const content = state.courseId ? `<a class="text-button" href="#course/${encodeURIComponent(state.courseId)}/section/${encodeURIComponent(link.section)}/canvas">Edit this section’s Canvas</a><a class="text-button" href="#course/${encodeURIComponent(state.courseId)}/home/canvas">All sections’ Canvas content</a>` : "";
    slot.innerHTML = linked
      ? `<a class="course-gradescope-course" href="${escape(link.url)}/courses/${escape(linked.course_id)}" target="_blank" rel="noopener noreferrer">${escape(linked.name || linked.code)}</a>${content}<button type="button" class="text-button" data-canvas-roster>Compare roster…</button><button type="button" class="text-button" data-canvas-change>Change…</button>`
      : `<span class="course-gradescope-course">No Canvas course linked</span><button type="button" class="text-button" data-canvas-change>Link a Canvas course…</button>`;
    slot.querySelector("[data-canvas-change]").onclick = () => void canvasDialog(link);
    const roster = slot.querySelector("[data-canvas-roster]");
    if (roster) roster.onclick = () => void canvasRosterDialog(link);
  }

  /** The course Home's Canvas row: where the term's Canvas content stands, from Faculty's records (Canvas is not contacted). */
  async function loadCanvasHome(group) {
    const row = document.querySelector("[data-canvas-home-row]");
    const slot = row?.querySelector("[data-canvas-home-status]");
    let overview;
    try {
      overview = await api("/api/canvas/overview");
    } catch {
      return;
    }
    if (!slot?.isConnected || !row) return;
    if (!overview?.canvas?.configured) { row.remove(); return; }
    const term = (overview.terms || []).find((item) => item.group === group.id);
    slot.textContent = term ? canvasStatusText(term) : "";
    // SYNC EVERY SECTION (requirement, 2026-10-06):
    // once a section links a Canvas course, the course Home's Sync grades opens Sync with Canvas with every course ticked
    // (gradebook.js openTermSync). A section tab's own Sync with Canvas stays as it is.
    const sync = row.querySelector("[data-course-canvas-sync]");
    if (canvasSync && (term?.courses || []).length) {
      if (!sync) slot.insertAdjacentHTML("afterend", '<button type="button" class="button" data-outward data-course-canvas-sync aria-label="Sync every section’s grades with Canvas">Sync grades</button>');
    } else sync?.remove();
  }

  /** The course Home's Grades row: the term's grading scheme in one line, from the gradebook's term list (catalog rows alone).
   * A term without a gradebook yet (no roster) has no row. */
  async function loadGradingScheme(group) {
    let data;
    try {
      data = await api("/api/gradebook");
    } catch {
      return;
    }
    const row = document.querySelector("[data-grading-scheme-row]");
    const slot = row?.querySelector("[data-grading-scheme-status]");
    if (!row?.isConnected || !slot || selectedCourseGroup()?.id !== group.id) return;
    const term = (data?.terms || []).find((item) => item.group_id === group.id);
    if (!term) { row.remove(); return; }
    showScheme(group.id, term.scale);
  }

  function showScheme(groupId, scale) {
    const slot = document.querySelector("[data-grading-scheme-status]");
    const text = scaleSummary(scale);
    schemeShown.set(groupId, text);
    if (slot && selectedCourseGroup()?.id === groupId && slot.textContent !== text) slot.textContent = text;
  }

  /** The course Home's Grading scheme: the dialog for the term's letter scale, busy until it has opened (or said why not). */
  async function editGradingScheme(button) {
    const group = selectedCourseGroup();
    if (!group || !gradingScheme || button.getAttribute("aria-busy") === "true") return;
    const active = captureDialogIntent($("#dialog"));
    setBusy(button, true);
    try {
      await gradingScheme({ groupId: group.id, active, onSaved: (scale) => showScheme(group.id, scale) });
    } finally {
      setBusy(button, false);
    }
  }

  /** The course Home's Sync grades: Sync with Canvas for the whole term, busy until the dialog has opened (or said why not). */
  async function syncGrades(button) {
    const group = selectedCourseGroup();
    if (!group || !canvasSync || button.getAttribute("aria-busy") === "true") return;
    const active = captureDialogIntent($("#dialog"));
    setBusy(button, true);
    try {
      await canvasSync({ groupId: group.id, active });
    } finally {
      setBusy(button, false);
    }
  }

  /** Reads Canvas; a 401 opens the sign-in window and, once signed in, `again()` reopens the step. Returns null when it did. */
  async function readCanvas(path, again, active) {
    try {
      return await api(path, "GET", undefined, {}, 120000);
    } catch (error) {
      if (error?.status !== 401) throw error;
      if (!active()) return null;
      if (await signInToCanvas()) again();
      return null;
    }
  }

  const showFormError = (form, error) => {
    const alert = form.querySelector("#form-error");
    alert.textContent = error.message;
    alert.hidden = false;
  };

  async function canvasDialog(link) {
    const base = link.linked?.course_id || "";
    showDialog(`<form id="canvas-link-form">${dialogHeader("Canvas course", `Section ${escape(link.section)} · ${escape(link.term_label)}`)}<div class="dialog-body"><p>This section’s Canvas course. Faculty reads its roster there, and later posts scores to it.</p><label class="field">Canvas course<select name="course_id" required disabled><option value="">Reading your Canvas courses…</option></select></label><p class="field-hint" data-canvas-proposed hidden>Proposed from the course and section numbers and the term. Check it before saving.</p><p class="field-hint" data-canvas-who hidden></p><p id="form-error" class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="text-button" data-canvas-forget hidden>Forget Faculty’s Canvas session</button><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary" disabled>Save</button></footer></form>`);
    const form = $("#canvas-link-form");
    const active = dialogSession(form, $("#dialog"));
    const select = form.elements.course_id, submit = form.querySelector('[type="submit"]');
    let result;
    try {
      result = await readCanvas(canvasRoute(link.section_id, "/courses"), () => void canvasDialog(link), active);
    } catch (error) {
      if (!active()) return;
      select.innerHTML = '<option value="">Canvas could not be read</option>';
      showFormError(form, error);
      return;
    }
    if (!result || !active()) return;
    const chosen = base || result.proposed || "";
    const label = (c) => `${c.name || c.code}${c.term ? ` (${c.term})` : ""}${c.state === "unpublished" ? " · unpublished" : ""}`;
    select.innerHTML = `<option value="" ${chosen ? "" : "selected"} disabled>Choose a Canvas course…</option>${(result.courses || []).map((c) => `<option value="${escape(c.id)}" ${String(c.id) === chosen ? "selected" : ""}>${escape(label(c))}</option>`).join("")}`;
    form.querySelector("[data-canvas-proposed]").hidden = Boolean(base) || !result.proposed;
    const who = form.querySelector("[data-canvas-who]");
    if (result.user?.name) { who.textContent = `Signed in to Canvas as ${result.user.name}.`; who.hidden = false; }
    const forget = form.querySelector("[data-canvas-forget]");
    forget.hidden = false;
    forget.onclick = async () => {
      setBusy(forget, true);
      try {
        await api("/api/canvas/forget", "POST", {});
        if (active()) $("#dialog").close();
        toast("Faculty forgot its Canvas session. Your own browser stays signed in.");
      } catch (error) {
        if (active()) showFormError(form, error);
        setBusy(forget, false);
      }
    };
    select.disabled = false;
    submit.disabled = false;
    select.focus();
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!select.value) return;
      await saveForm(
        form,
        () => api(canvasRoute(link.section_id), "POST", { course_id: select.value, base }, {}, 120000),
        "Canvas course saved.",
        () => void loadCanvas(link.section_id),
      );
    });
  }

  function namesMarkup(title, rows, key, detail = () => "") {
    if (!rows.length) return "";
    return `<details class="disclosure" ${rows.length <= 12 ? "open" : ""}><summary><span>${escape(title)}</span><span class="disclosure-hint">${rows.length}</span></summary><div class="disclosure-body"><ul class="canvas-roster-names">${rows.map((row) => `<li>${escape(typeof row === "string" ? row : row[key])}${detail(row) ? ` <small>· ${escape(detail(row))}</small>` : ""}</li>`).join("")}</ul></div></details>`;
  }

  async function canvasRosterDialog(link, notice = "") {
    const group = selectedCourseGroup();
    showDialog(`<div id="canvas-roster">${dialogHeader("Roster in Canvas", `Section ${escape(link.section)} · ${escape(link.linked?.name || "")}`)}<div class="dialog-body" data-canvas-roster-body>${loadingMarkup("Reading the Canvas roster…", 2)}</div><footer class="dialog-footer"><button type="button" class="button primary" data-action="close">Close</button></footer></div>`);
    const body = $("[data-canvas-roster-body]");
    const active = dialogSession(body, $("#dialog"));
    let result;
    try {
      result = await readCanvas(canvasRoute(link.section_id, "/roster"), () => void canvasRosterDialog(link), active);
    } catch (error) {
      if (active()) body.innerHTML = `<p class="form-error" role="alert">${escape(error.message)}</p>`;
      return;
    }
    if (!result || !active()) return;
    const c = result.counts;
    const same = !c.only_faculty && !c.only_canvas && !c.no_id;
    // Students only in Faculty can be dropped from this section (requirement, 2026-09-27): a recorded, reversible mark; their
    // records stay, and they leave new seating, card issues and exams. Students dropped here whom Canvas still lists can be restored.
    const drop = result.only_faculty.length ? `<details class="disclosure" open><summary><span>In Faculty’s roster, not in Canvas</span><span class="disclosure-hint">${result.only_faculty.length}</span></summary><div class="disclosure-body">
      <ul class="canvas-roster-names canvas-roster-pick">${result.only_faculty.map((row) => `<li><label class="grading-check"><input type="checkbox" data-canvas-drop="${escape(row.enrollment_id)}">${escape(row.name)}</label></li>`).join("")}</ul>
      <p class="field-hint">Dropping keeps everything already recorded for them (papers, grades, a seat they hold) and can be undone from the roster. They are no longer seated, issued cards or added to exams.</p>
      <div class="canvas-roster-actions"><button type="button" class="text-button" data-canvas-all>Select all</button><button type="button" class="button" data-canvas-drop-selected disabled>Drop selected from Section ${escape(result.section)}</button></div>
      <div data-canvas-confirm hidden class="callout" data-tone="warning"><p data-canvas-confirm-text></p><div class="canvas-roster-actions"><button type="button" class="button" data-canvas-cancel>Cancel</button><button type="button" class="button danger" data-canvas-confirm-drop>Drop</button></div></div></div></details>` : "";
    const restore = result.dropped_in_canvas.length ? `<details class="disclosure" open><summary><span>Dropped here, but Canvas still lists them</span><span class="disclosure-hint">${result.dropped_in_canvas.length}</span></summary><div class="disclosure-body"><ul class="canvas-roster-names">${result.dropped_in_canvas.map((row) => `<li>${escape(row.name)} <button type="button" class="text-button" data-canvas-restore="${escape(row.enrollment_id)}">Restore</button></li>`).join("")}</ul></div></details>` : "";
    body.innerHTML = `${notice}${result.ids_shared ? "" : '<div class="callout" data-tone="warning"><p>Canvas did not share its students’ university IDs with Faculty, so nobody could be matched.</p></div>'}
      <p>${same ? `All ${c.faculty} current students of Section ${escape(result.section)} are in Canvas, and Canvas lists no one else.` : `${c.both} of Faculty’s ${c.faculty} current students are in Canvas; Canvas lists ${c.canvas}.`} Matched by UIN (Canvas’s SIS ID) exactly.${c.dropped ? ` ${c.dropped} dropped here.` : ""}</p>
      ${drop}
      ${namesMarkup("In Canvas, not in any section’s roster here", result.only_canvas, "name", (row) => row.canvas_section)}
      ${namesMarkup("In Canvas, in another section’s roster here", result.other_section, "name", (row) => `Section ${row.faculty_section}`)}
      ${namesMarkup("In Canvas without a university ID", result.no_id, "name", (row) => row.canvas_section)}
      ${restore}<p id="form-error" class="form-error" role="alert" hidden></p>`;
    const route = (action) => `${rosterRoute(group.id, link.section)}/${action}`;
    const fail = (error) => { if (active()) showFormError(body, error); };
    const after = (text) => { void loadRosterPanel(); refreshCounts(true); if (active()) void canvasRosterDialog(link, `<div class="callout" data-tone="success" role="status"><p>${escape(text)}</p></div>`); };
    const boxes = [...body.querySelectorAll("[data-canvas-drop]")];
    const dropButton = body.querySelector("[data-canvas-drop-selected]"), confirmBox = body.querySelector("[data-canvas-confirm]");
    const chosen = () => boxes.filter((box) => box.checked).map((box) => box.dataset.canvasDrop);
    boxes.forEach((box) => { box.onchange = () => { dropButton.disabled = !chosen().length; confirmBox.hidden = true; }; });
    const all = body.querySelector("[data-canvas-all]");
    if (all) all.onclick = () => { boxes.forEach((box) => { box.checked = true; }); dropButton.disabled = !boxes.length; };
    if (dropButton) dropButton.onclick = () => {
      const ids = chosen();
      body.querySelector("[data-canvas-confirm-text]").textContent = `Drop ${ids.length} student${ids.length === 1 ? "" : "s"} from Section ${result.section}? Canvas no longer lists ${ids.length === 1 ? "them" : "any of them"}. You can restore a dropped student from the roster.`;
      confirmBox.hidden = false;
      body.querySelector("[data-canvas-confirm-drop]").focus();
    };
    const cancel = body.querySelector("[data-canvas-cancel]");
    if (cancel) cancel.onclick = () => { confirmBox.hidden = true; };
    const confirmDrop = body.querySelector("[data-canvas-confirm-drop]");
    if (confirmDrop) confirmDrop.onclick = async () => {
      const ids = chosen();
      setBusy(confirmDrop, true);
      try {
        const saved = await api(route("drop"), "POST", { enrollment_ids: ids, request_id: crypto.randomUUID(), source: "canvas", reason: "Not in Canvas" });
        after(`Dropped ${saved.changed} student${saved.changed === 1 ? "" : "s"} from Section ${result.section}.`);
      } catch (error) {
        setBusy(confirmDrop, false);
        fail(error);
      }
    };
    body.querySelectorAll("[data-canvas-restore]").forEach((button) => {
      button.onclick = async () => {
        setBusy(button, true);
        try {
          await api(route("restore"), "POST", { enrollment_ids: [button.dataset.canvasRestore], request_id: crypto.randomUUID(), source: "canvas" });
          after("Restored to the roster.");
        } catch (error) {
          setBusy(button, false);
          fail(error);
        }
      };
    });
  }

  /* ---- Storage -------------------------------------------------------------------------------------------------------- */

  function courseStorageLabel(course) {
    if (!state.storage.enabled) return "";
    const labels = {
      active: "Working on NVMe",
      archived: "Stored on HDD",
      restore_pending: "Preparing NVMe copy…",
      sync_pending: "Updating NVMe copy…",
      archive_pending: "Copying and verifying on HDD…",
      purge_pending: "HDD verified · clearing NVMe copy…",
    };
    return `<div class="course-storage ${course.storage_error ? "storage-warning" : ""}"><span>${escape(labels[course.storage_status] || "Preparing storage…")}</span><small>${course.storage_verified_at ? `This course’s HDD copy verified ${escape(storageDate(course.storage_verified_at))}` : "First HDD copy pending"}</small>${course.storage_error ? `<small>Needs attention: ${escape(course.storage_error)}</small>` : ""}</div>`;
  }

  /**
   * Course storage and backups. The settings dialog shows the full box; the course list (`inline`) shows one quiet line while
   * everything is healthy and a callout only when a backup failed or was interrupted, an issue is open, or course storage is
   * still being moved. An incomplete backup is never called verified.
   */
  function storageSummary({ inline = false } = {}) {
    const storage = state.storage;
    if (!storage.enabled)
      return inline
        ? '<p class="storage-line">Course backups are not configured · set up the NVMe and HDD storage service on your server to turn them on.</p>'
        : '<div class="storage-summary"><strong>Course backups are not configured</strong><p>Set up the NVMe and HDD storage service on your server to enable automatic backups.</p></div>';
    const backup = storage.last_backup || {};
    const failed =
      ["failed", "interrupted"].includes(backup.status) || storage.issues?.length;
    let detail = "The first nightly backup is pending.";
    if (backup.status === "complete")
      detail = `Last backup verified ${storageDate(backup.finished_at)}.`;
    if (backup.status === "running") detail = "A backup is running now.";
    if (backup.status === "failed")
      detail =
        "The last backup failed. Working copies are retained until their final backup is verified.";
    if (backup.status === "interrupted")
      detail =
        "The last backup was interrupted. Check the backup service on your server.";
    const pending = storage.pending_count ? `${storage.pending_count} course storage operation${storage.pending_count === 1 ? "" : "s"} pending.` : "";
    const problems = `${(storage.issues || []).map((issue) => `<p>${escape(issue.name)}: ${escape(issue.message)}</p>`).join("")}${(backup.errors || []).map((message) => `<p>${escape(message)}</p>`).join("")}`;
    if (inline) {
      const schedule = `Current courses work on NVMe · HDD backups run nightly at ${escape(storage.backup_time)}`;
      if (failed)
        return `<div class="callout storage-callout" data-tone="danger" role="alert"><strong class="callout-title">Course storage needs attention</strong><p>${escape(detail)}${pending ? ` ${escape(pending)}` : ""}</p>${problems}<p class="storage-schedule">${schedule}.</p></div>`;
      if (pending)
        return `<div class="callout storage-callout" data-tone="info" role="status"><strong class="callout-title">Course storage is being updated</strong><p>${escape(pending)} Settings of a course return when its move finishes.</p><p class="storage-schedule">${schedule} · ${escape(detail.replace(/\.$/, ""))}.</p></div>`;
      return `<p class="storage-line">${schedule} · ${escape(detail.replace(/\.$/, ""))}</p>`;
    }
    return `<div class="storage-summary ${failed ? "storage-warning" : ""}"><strong>${failed ? "Course storage needs attention" : "Course storage & backups"}</strong><p>Current courses use NVMe. HDD backups run nightly at ${escape(storage.backup_time)}.</p><p>${escape(detail)}${pending ? ` ${escape(pending)}` : ""}</p>${problems}</div>`;
  }

  /* ---- Section roster ------------------------------------------------------------------------------------------------- */

  let rosterRequest = 0;
  const rosterViews = new WeakMap();

  function rosterRoute(groupId, section) {
    return `/api/course-groups/${encodeURIComponent(groupId)}/sections/${section}/roster`;
  }

  async function loadRosterPanel() {
    const node = $("#section-roster");
    if (!node || state.view !== "course" || state.courseSection === null) return;
    const requestNumber = ++rosterRequest;
    try {
      const data = await api(
        rosterRoute(node.dataset.groupId, node.dataset.section),
      );
      if (requestNumber !== rosterRequest || !node.isConnected) return;
      renderRoster(node, data);
    } catch (error) {
      if (requestNumber !== rosterRequest || !node.isConnected) return;
      node.dataset.signature = "";
      node.innerHTML = `<div class="empty-state"><h3>Roster unavailable</h3><p role="alert">${escape(error.message)}</p><button class="button" data-action="retry-roster">Try again</button></div>`;
    }
  }

  function rosterView(node) {
    if (!rosterViews.has(node)) rosterViews.set(node, { query: "", filter: "all", sort: null, scrollLeft: 0, data: null });
    return rosterViews.get(node);
  }

  const fieldOf = (student, name) => {
    const entry = Object.entries(student.fields || {}).find(([key]) => key.toLowerCase() === name);
    return entry ? String(entry[1] ?? "").trim() : "";
  };

  function nextBooking(student, today = localToday()) {
    const bookings = student.dr_exams || [];
    return bookings.find((booking) => String(booking.date) >= today) || null;
  }

  function supportCell(student) {
    const parts = [];
    const record = student.accommodations;
    if (record) {
      const metadata = record.metadata || {};
      const detail = Object.entries(metadata).filter(([key, value]) => value && ["notes", "request status", "access coordinator", "access coordinator email"].includes(key.toLowerCase()));
      parts.push(`<details class="student-accommodations"><summary><span class="chip" data-tone="info">ACC ${record.items.length}</span><span class="sr-only">${record.items.length ? `${record.items.length} accommodation${record.items.length === 1 ? "" : "s"}` : "No active accommodations"}</span></summary>
    ${record.items.length ? `<ul>${record.items.map((item) => `<li>${escape(item.name)}${item.value.toLowerCase() === "yes" ? "" : `: ${escape(item.value)}`}</li>`).join("")}</ul>` : "<p>No active accommodations</p>"}
    ${detail.map(([key, value]) => `<p><strong>${escape(key)}:</strong> ${escape(value)}</p>`).join("")}
    <p class="field-hint">Updated ${escape(record.updated_at?.slice(0, 10) || "date not supplied")}</p><a href="#inbox/job/${escape(record.job_id)}">View import record</a></details>`);
    }
    if (student.dr_exams?.length) {
      const next = nextBooking(student);
      const count = student.dr_exams.length;
      parts.push(`<details class="student-accommodations"><summary><span class="chip" data-tone="neutral">DR${next ? ` · next ${escape(wallDate(next.date).replace(/^\w+, /, ""))}` : ` ${count}`}</span></summary><p><strong>${count} DR exam booking${count === 1 ? "" : "s"}</strong></p>${student.dr_exams.map((booking) => `<p>${escape(scheduleText(booking))}</p>`).join("")}<a href="#accommodations?tab=exams">View DR exam schedule</a></details>`);
    }
    return parts.length ? `<div class="roster-support">${parts.join("")}</div>` : '<span class="field-hint">—</span>';
  }

  function rosterMarkup(node, data) {
    const view = rosterView(node);
    const showEmpty = readShowEmpty();
    const columns = rosterColumns(data.headers, data.students);
    const support = Boolean(data.accommodation_count || data.dr_exam_count || data.students.some((student) => student.accommodations || student.dr_exams?.length));
    const shown = [
      ...columns.lead.map((name) => ({ key: `field:${name}`, label: name, name })),
      ...(support ? [{ key: "support", label: "Support" }] : []),
      ...columns.rest.map((name) => ({ key: `field:${name}`, label: name, name })),
      ...(showEmpty ? columns.empty.map((name) => ({ key: `field:${name}`, label: name, name })) : []),
      { key: "private", label: "Private course ID" },
    ];
    const emailHeader = data.headers.find((name) => name.toLowerCase() === "email");
    const nameHeader = data.headers.find((name) => name.toLowerCase() === "name");
    const current = data.students.filter((student) => !student.dropped);
    const withAccommodations = current.filter((student) => student.accommodations?.items?.length).length;
    const withBookings = current.filter((student) => student.dr_exams?.length).length;
    // Dropped students (rosters.set_dropped) are kept on the roster, behind their own filter.
    const chips = support || data.dropped_count
      ? filterChipsMarkup("roster-support", [
          { value: "all", label: "All students", count: data.count },
          ...(support ? [{ value: "accommodations", label: "With accommodations", count: withAccommodations },
            { value: "dr", label: "With DR bookings", count: withBookings }] : []),
          ...(data.dropped_count ? [{ value: "dropped", label: "Dropped", count: data.dropped_count }] : []),
        ], view.filter, { label: "Show students" })
      : "";
    const still = data.last_import?.still_dropped
      ? `<div class="callout" data-tone="info" role="status"><p>The latest import (${escape(data.last_import.filename)}) still lists ${data.last_import.still_dropped} student${data.last_import.still_dropped === 1 ? "" : "s"} you dropped. ${data.last_import.still_dropped === 1 ? "It stays" : "They stay"} dropped; restore from the Dropped list if that was a mistake.</p></div>`
      : "";
    // Students the latest import dropped because the registrar's roster marks them (requirement, 2026-10-25), while they stay dropped.
    const droppedIds = new Set(data.students.filter((student) => student.dropped).map((student) => student.id));
    const autoDropped = (data.last_import?.registrar_dropped || []).filter((row) => droppedIds.has(row.id));
    const registrar = autoDropped.length
      ? `<div class="callout" data-tone="info" role="status" data-roster-registrar-dropped><p>The latest import (${escape(data.last_import.filename)}) dropped ${autoDropped.length === 1 ? "this student" : `these ${autoDropped.length} students`}, as the registrar's roster marks them:</p><ul class="canvas-roster-names">${autoDropped.map((row) => `<li>${escape(row.name)} · ${escape(row.reason)}</li>`).join("")}</ul><p>Everything recorded for them stays; restore from the Dropped list if that was a mistake.</p></div>`
      : "";
    // Students the registrar's roster says are leaving and are still current (rosters.leaving: not on the latest import, or a mark
    // an import before 2026-10-25 left current), offered for the roster's own Drop after one confirmation.
    const leaving = data.leaving?.length
      ? `<div class="callout" data-tone="warning" role="status" data-roster-leaving><p>The registrar's roster says ${data.leaving.length === 1 ? "this student is" : `these ${data.leaving.length} students are`} leaving Section ${escape(data.section)}:</p><ul class="canvas-roster-names">${data.leaving.map((row) => `<li>${escape(row.name)} · ${escape(row.reason)}</li>`).join("")}</ul><div class="canvas-roster-actions"><button type="button" class="button" data-roster-leaving-drop>Drop ${data.leaving.length === 1 ? escape(data.leaving[0].name) : `all ${data.leaving.length}`}…</button></div><div data-roster-leaving-confirm hidden><p>Drop ${data.leaving.length === 1 ? escape(data.leaving[0].name) : `these ${data.leaving.length} students`} from Section ${escape(data.section)}? Everything recorded for them stays; they are no longer seated, issued cards, added to exams, emailed with the section or sent to Canvas. You can restore them from the Dropped list.</p><div class="canvas-roster-actions"><button type="button" class="button" data-roster-leaving-cancel>Cancel</button><button type="button" class="button danger" data-roster-leaving-yes>Drop</button></div></div></div>`
      : "";
    const cell = (student, column) => {
      if (column.key === "private") return `<td class="private-student-id">${escape(student.id)}</td>`;
      if (column.key === "support") return `<td class="roster-support-cell">${supportCell(student)}</td>`;
      const value = rosterValue(student.fields[column.name]);
      if (column.name === nameHeader)
        return `<td class="roster-name"><button type="button" class="roster-student" data-roster-student="${escape(student.id)}" title="${escape(value || student.uin)} · show everything saved for this student">${escape(value || student.uin)}</button></td>`;
      return `<td${value.includes("\n") ? ' class="multiline"' : ""}>${escape(value)}</td>`;
    };
    const sortState = (column) => (view.sort?.key === column.key ? (view.sort.dir === "asc" ? "ascending" : "descending") : "none");
    return `<div class="roster-heading"><div><h3>Roster <span class="count-badge">${data.count}</span></h3><p class="roster-summary">${escape(rosterSummary(data))}</p></div><div class="roster-actions"><a class="link-arrow" href="#accommodations" aria-label="Accommodations & DR exams">Accommodations & DR exams</a><button class="button" data-action="import-roster">${icon("plus")}Import roster</button></div></div>
      <div class="toolbar roster-toolbar"><label class="field roster-search">Find a student<input type="search" id="roster-search" data-page-search placeholder="Name, UIN, email or private ID" autocomplete="off" value="${escape(view.query)}"></label>${chips}<span class="toolbar-actions">${emailHeader ? `<button type="button" class="button" data-roster-copy>Copy emails</button>` : ""}${columns.empty.length ? `<button type="button" class="text-button" data-roster-empty aria-pressed="${showEmpty}">${showEmpty ? `Hide ${columns.empty.length} empty column${columns.empty.length === 1 ? "" : "s"}` : `Show ${columns.empty.length} empty column${columns.empty.length === 1 ? "" : "s"}`}</button>` : ""}</span></div>
      ${still}${registrar}${leaving}<p id="roster-filter-count" class="field-hint" role="status"></p>
      <div class="roster-table-scroll" tabindex="0" role="region" aria-label="Student roster; scroll to see all imported fields"><table class="roster-table"><thead><tr>${shown.map((column) => `<th scope="col" aria-sort="${sortState(column)}"><button type="button" class="roster-sort" data-sort-key="${escape(column.key)}">${escape(column.label)}<span class="roster-sort-mark" aria-hidden="true"></span></button></th>`).join("")}</tr></thead><tbody>${data.students.map((student) => `<tr data-student="${escape(student.id)}" data-search="${escape([student.id, student.uin, ...Object.values(student.fields)].join(" ").toLowerCase())}" data-accommodations="${student.accommodations?.items?.length ? "true" : "false"}" data-dr="${student.dr_exams?.length ? "true" : "false"}" data-dropped="${student.dropped ? "true" : "false"}" data-email="${escape(emailHeader ? String(student.fields[emailHeader] ?? "").trim() : "")}">${shown.map((column) => cell(student, column)).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  function sortKey(row, index) {
    const cellNode = row.children[index];
    return (cellNode?.textContent || "").trim().toLowerCase();
  }

  function applyRosterView(node) {
    const view = rosterView(node);
    const data = view.data;
    if (!data) return;
    const tbody = node.querySelector("tbody");
    if (!tbody) return;
    const headers = [...node.querySelectorAll("thead th")];
    if (view.sort) {
      const index = headers.findIndex((th) => th.querySelector("[data-sort-key]")?.dataset.sortKey === view.sort.key);
      if (index >= 0) {
        const direction = view.sort.dir === "asc" ? 1 : -1;
        const rows = [...tbody.rows].sort((a, b) => direction * sortKey(a, index).localeCompare(sortKey(b, index), undefined, { numeric: true, sensitivity: "base" }));
        rows.forEach((row) => tbody.appendChild(row));
      }
      headers.forEach((th) => th.setAttribute("aria-sort", th.querySelector("[data-sort-key]")?.dataset.sortKey === view.sort.key ? (view.sort.dir === "asc" ? "ascending" : "descending") : "none"));
    }
    const query = view.query.toLowerCase().trim();
    let visible = 0;
    [...tbody.rows].forEach((row) => {
      const dropped = row.dataset.dropped === "true";
      const matches = (!query || row.dataset.search.includes(query)) && (view.filter === "dropped" ? dropped : !dropped
        && (view.filter === "all" || (view.filter === "accommodations" ? row.dataset.accommodations === "true" : row.dataset.dr === "true")));
      row.hidden = !matches;
      if (matches) visible++;
    });
    const status = node.querySelector("#roster-filter-count");
    const text = query || view.filter !== "all" ? `${visible} of ${view.filter === "dropped" ? data.dropped_count : data.count} ${view.filter === "dropped" ? "dropped " : ""}students` : "";
    if (status && status.textContent !== text) status.textContent = text;
  }

  function bindRoster(node) {
    if (node.dataset.bound === "true") return;
    node.dataset.bound = "true";
    node.addEventListener("input", (event) => {
      if (event.target.id !== "roster-search") return;
      rosterView(node).query = event.target.value;
      applyRosterView(node);
    });
    node.addEventListener("scroll", (event) => {
      if (event.target.classList?.contains("roster-table-scroll")) rosterView(node).scrollLeft = event.target.scrollLeft;
    }, true);
    bindFilterChips(node, "roster-support", (value) => {
      rosterView(node).filter = value;
      applyRosterView(node);
    });
    node.addEventListener("click", (event) => {
      const sort = event.target.closest("[data-sort-key]");
      if (sort) {
        const view = rosterView(node);
        const key = sort.dataset.sortKey;
        view.sort = { key, dir: view.sort?.key === key && view.sort.dir === "asc" ? "desc" : "asc" };
        applyRosterView(node);
        return;
      }
      if (event.target.closest("[data-roster-empty]")) {
        writeShowEmpty(!readShowEmpty());
        redrawRoster(node, rosterView(node).data);
        node.querySelector("[data-roster-empty]")?.focus();
        return;
      }
      if (event.target.closest("[data-roster-copy]")) {
        void copyEmails(node);
        return;
      }
      if (event.target.closest("[data-roster-leaving-drop]")) {
        const confirmBox = node.querySelector("[data-roster-leaving-confirm]");
        confirmBox.hidden = false;
        confirmBox.querySelector("[data-roster-leaving-yes]").focus();
        return;
      }
      if (event.target.closest("[data-roster-leaving-cancel]")) {
        node.querySelector("[data-roster-leaving-confirm]").hidden = true;
        return;
      }
      const dropLeaving = event.target.closest("[data-roster-leaving-yes]");
      if (dropLeaving) {
        void dropLeavingStudents(node, dropLeaving);
        return;
      }
      const student = event.target.closest("[data-roster-student]");
      if (student) studentDialog(node, student.dataset.rosterStudent);
    });
  }

  // One drop request per reason, so each student's drop records why (Q drop, Withdrawn, not on the latest roster).
  async function dropLeavingStudents(node, button) {
    const data = rosterView(node).data;
    const groups = new Map();
    for (const row of data?.leaving || []) groups.set(row.reason, [...(groups.get(row.reason) || []), row.id]);
    setBusy(button, true);
    let changed = 0;
    try {
      for (const [reason, ids] of groups) {
        const saved = await api(`${rosterRoute(node.dataset.groupId, node.dataset.section)}/drop`, "POST",
          { enrollment_ids: ids, request_id: crypto.randomUUID(), source: "roster", reason });
        changed += saved.changed;
      }
      toast(`Dropped ${changed} student${changed === 1 ? "" : "s"} from Section ${data.section}.`);
    } catch (error) {
      toast(error.message, true);
    } finally {
      setBusy(button, false);
    }
    void loadRosterPanel();
    refreshCounts(true);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Clipboard access can be refused (older browsers, plain HTTP): copy through a hidden text area instead.
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.className = "sr-only";
      document.body.append(area);
      area.select();
      let copied = false;
      try { copied = document.execCommand("copy"); } catch { copied = false; }
      area.remove();
      return copied;
    }
  }

  async function copyEmails(node) {
    const emails = [...node.querySelectorAll("tbody tr:not([hidden])")].map((row) => row.dataset.email).filter(Boolean);
    const unique = [...new Set(emails)];
    if (!unique.length) {
      toast("No email addresses in the students shown.", true);
      return;
    }
    const copied = await copyText(unique.join(", "));
    toast(copied ? `Copied ${unique.length} email address${unique.length === 1 ? "" : "es"}.` : "Your browser did not allow copying. Select the emails in the table instead.", !copied);
  }

  // A student's card from the roster (rest-people, 2026-10-02): who they are in one line, then what you keep about them (support,
  // notes, absences, messages) and the ways to act, with the imported roster fields folded below. Every window opened from here
  // (the timeline, an absence, their mail) offers "Back to <name>".
  function studentDialog(node, id) {
    const data = rosterView(node).data;
    const student = data?.students.find((item) => item.id === id);
    const group = selectedCourseGroup();
    if (!student || !group) return false;
    const name = fieldOf(student, "name") || student.uin;
    const email = fieldOf(student, "email") || fieldOf(student, "email address") || fieldOf(student, "e-mail");
    // "Example, Alice" → "Alice", as the timeline's "Write to …" says it.
    const first = firstName(name) || "them";
    const section = Number(node.dataset.section);
    const fields = data.headers.map((header) => [header, String(student.fields[header] ?? "").trim()]);
    const record = student.accommodations;
    // The Accommodations page's own view: the items as chips, the coordinator, notes and source in a closed disclosure
    // (the export's name, UIN and course columns repeat the dialog's header and are left out).
    const accommodations = record ? accommodationTags(record, escape) : "<p>No accommodation record.</p>";
    const bookings = student.dr_exams?.length
      ? `<ul>${student.dr_exams.map((booking) => `<li>${escape(scheduleText(booking))}${booking.assignment?.title ? ` · linked to ${escape(booking.assignment.title)}` : ""}</li>`).join("")}</ul>`
      : "<p>No DR exam bookings.</p>";
    const facts = `<p class="student-facts section-lead">${escape(["UIN " + (student.uin || "—"), email || "No email on the roster", `Section ${section}`].join(" · "))}</p>`;
    const rosterFields = `<dl class="student-fields"><div><dt>Private course ID</dt><dd class="private-student-id">${escape(student.id)}</dd></div>${fields.map(([label, value]) => `<div><dt>${escape(label)}</dt><dd${value.includes("\n") ? ' class="multiline"' : ""}>${value ? escape(value) : '<span class="field-hint">—</span>'}</dd></div>`).join("")}</dl>`;
    const write = compose && email ? `<button type="button" class="text-button" data-student-write>${escape(`Write to ${first}…`)}</button>` : "";
    const reopened = $("#dialog").open;  // "Back to <name>" (or a save) from a window opened here
    const links = `<p class="student-quick-links"><a class="link-arrow" href="${gradebookHref(group, section)}" aria-label="Section ${section} gradebook" data-action="close">Open in gradebook</a><a class="link-arrow" href="#accommodations" aria-label="Accommodations & DR exams" data-action="close">Accommodations & DR exams</a>${write}${communications ? '<button type="button" class="text-button" data-student-communications>Communications…</button>' : studentMail ? '<button type="button" class="text-button" data-student-mail>Mail with this student…</button>' : ""}</p>`;
    showDialog(`${dialogHeader(escape(name), `${escape(group.code)} · ${escape(courseTerm(group))} · Section ${section}`)}<div class="dialog-body student-quick-view">${facts}${student.photo ? `<img class="student-photo" src="/api/student-photos/${encodeURIComponent(student.id)}?v=${encodeURIComponent(student.photo)}" alt="Photo of ${escape(name)}" width="128" height="128" />` : ""}<h3>Accommodations</h3>${accommodations}<h3>DR exam bookings</h3>${bookings}${studentNotesPanel ? '<h3>Notes</h3><div data-student-notes></div>' : ""}${absencesPanel && data.section_id ? '<h3>Absences</h3><div data-absences-card></div>' : ""}${communicationsSummary ? '<h3>Communications</h3><div data-comm-summary></div>' : ""}${links}${disclosureMarkup({ label: "Roster record", hint: `${fields.length + 1} fields`, key: "roster:record", body: rosterFields, className: "student-roster-record" })}${student.dropped ? `<div class="callout" data-tone="info"><p>Dropped from Section ${section}${student.dropped.source === "canvas" ? " after the Canvas roster check" : ""}${student.dropped.at ? ` on ${escape(wallDate(student.dropped.at.slice(0, 10)))}` : ""}. Their records are kept.</p></div>` : ""}<div class="callout" data-tone="warning" data-student-drop-confirm hidden><p>Drop ${escape(name)} from Section ${section}? Everything recorded for them stays (papers, grades, a seat they hold); they are no longer seated, issued cards or added to exams. You can restore them from the roster’s Dropped list.</p><div class="canvas-roster-actions"><button type="button" class="button" data-student-drop-cancel>Cancel</button><button type="button" class="button danger" data-student-drop-yes>Drop</button></div></div><p id="form-error" class="form-error" role="alert" hidden></p></div><footer class="dialog-footer">${student.dropped ? '<button type="button" class="button" data-student-restore>Restore to the roster</button>' : `<button type="button" class="text-button danger" data-student-drop>Drop from Section ${section}…</button>`}<button type="button" class="button primary" data-action="close">Close</button></footer>`);
    const dialog = $("#dialog");
    const active = dialogSession(dialog.querySelector(".student-quick-view"), dialog);
    const route = (action) => `${rosterRoute(group.id, section)}/${action}`;
    if (studentSupport) {
      const button = document.createElement("button");
      button.type = "button"; button.className = "text-button"; button.textContent = "Follow-up & office hours";
      button.onclick = () => studentSupport({enrollmentId: student.id, name});
      dialog.querySelector(".student-quick-links")?.append(button);
    }
    const change = async (action, button) => {
      setBusy(button, true);
      try {
        await api(route(action), "POST", { enrollment_ids: [student.id], request_id: crypto.randomUUID(), source: "roster" });
        if (active()) dialog.close();
        toast(action === "drop" ? `${name} was dropped from Section ${section}.` : `${name} is back on the roster.`);
        void loadRosterPanel();
        refreshCounts(true);
      } catch (error) {
        setBusy(button, false);
        if (!active()) return;
        showFormError(dialog, error);
      }
    };
    const confirmBox = dialog.querySelector("[data-student-drop-confirm]");
    const dropButton = dialog.querySelector("[data-student-drop]");
    if (dropButton) dropButton.onclick = () => { confirmBox.hidden = false; dialog.querySelector("[data-student-drop-yes]").focus(); };
    dialog.querySelector("[data-student-drop-cancel]").onclick = () => { confirmBox.hidden = true; };
    const yes = dialog.querySelector("[data-student-drop-yes]");
    yes.onclick = () => void change("drop", yes);
    const restore = dialog.querySelector("[data-student-restore]");
    if (restore) restore.onclick = () => void change("restore", restore);
    const context = `${group.code} · ${courseTerm(group)} · Section ${section}`;
    // Every window opened from this card comes back to it (the student read again from the roster on screen).
    const back = { label: `Back to ${name}`, run: () => { if (studentDialog(node, id) === false) dialog.close(); } };
    const mailInfo = { group: group.id, section, studentId: student.id, name, context, back };
    const mailButton = dialog.querySelector("[data-student-mail]");
    if (mailButton && studentMail) mailButton.onclick = () => void studentMail(mailInfo);
    // Write to them: a new message to their roster address, reviewed and sent only from the compose window's own Send.
    const writeTo = compose && email ? () => void compose({ to: email, context: `To ${name}` }) : null;
    const writeButton = dialog.querySelector("[data-student-write]");
    if (writeButton && writeTo) writeButton.onclick = writeTo;
    // The student's communications record: the newest entries here, the whole timeline in its own window (which can still search
    // Gmail live, as "Mail with this student…" did, and write to them).
    const onMail = studentMail ? () => void studentMail(mailInfo) : null;
    const openTimeline = () => void communications?.({ enrollmentId: student.id, name, context, onMail, onWrite: writeTo, back });
    const timelineButton = dialog.querySelector("[data-student-communications]");
    if (timelineButton instanceof HTMLElement) timelineButton.onclick = openTimeline;
    const summaryHost = dialog.querySelector("[data-comm-summary]");
    if (summaryHost instanceof HTMLElement && communicationsSummary) void communicationsSummary(summaryHost, student.id, openTimeline);
    // Your notes about them this term (student-notes.js): added, edited and deleted in place.
    const notesHost = dialog.querySelector("[data-student-notes]");
    if (notesHost instanceof HTMLElement && studentNotesPanel) void studentNotesPanel(notesHost, { enrollmentId: student.id, name });
    // Their absences in this section (absences.js): each opens the absence, and a new one is recorded from here.
    const absencesHost = dialog.querySelector("[data-absences-card]");
    if (absencesHost instanceof HTMLElement && absencesPanel) void absencesPanel(absencesHost, { sectionId: data.section_id, enrollmentId: student.id, name, section, back });
    // A fresh window focuses its first control; one that comes back in place (the dialog stayed open) focuses Close, as the
    // gradebook's record does, so the keyboard does not land on the page behind it.
    if (reopened) dialog.querySelector('.dialog-footer [data-action="close"]')?.focus();
    return true;
  }

  function redrawRoster(node, data) {
    const view = rosterView(node);
    view.data = data;
    const scroller = node.querySelector(".roster-table-scroll");
    if (scroller) view.scrollLeft = scroller.scrollLeft;
    const focused = document.activeElement?.id === "roster-search";
    const selection = focused ? [document.activeElement.selectionStart, document.activeElement.selectionEnd] : null;
    node.innerHTML = rosterMarkup(node, data);
    bindRoster(node);
    applyRosterView(node);
    const next = node.querySelector(".roster-table-scroll");
    if (next && view.scrollLeft) next.scrollLeft = view.scrollLeft;
    if (focused) {
      const input = node.querySelector("#roster-search");
      input?.focus({ preventScroll: true });
      if (selection && input) input.setSelectionRange(selection[0], selection[1]);
    }
  }

  function renderRoster(node, data) {
    const signature = JSON.stringify(data);
    if (node.dataset.signature === signature) return;
    node.dataset.signature = signature;
    if (!data.students.length) {
      rosterView(node).data = data;
      node.innerHTML = `<div class="empty-state roster-empty"><span class="empty-state-icon" aria-hidden="true">${icon("users")}</span><h3>Import this section’s roster</h3><p>Start with your CSV or Excel roster export. Each student receives a private course ID.</p><button class="button primary" data-action="import-roster">${icon("plus")}Import roster</button><small>CSV or XLSX · Up to 10 MB</small></div>`;
      return;
    }
    redrawRoster(node, data);
  }

  /* ---- Import roster dialogs ------------------------------------------------------------------------------------------ */

  const ACCEPT = ".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  const validFile = (file) => file && file.size <= 10 * 1024 * 1024 && /\.(csv|xlsx)$/i.test(file.name);
  const idsDisclosure = () => disclosureMarkup({ label: "How private IDs work", body: "<p>Returning students keep their five-digit prefix and receive a new suffix for each course and term. Reimporting, or adding them to another section of this course and term, keeps their full ID.</p>", className: "roster-import-info" });

  function importRosterDialog() {
    const group = selectedCourseGroup();
    const section = state.courseSection;
    if (!group || section === null || !group.sections.includes(section)) return;
    const route = rosterRoute(group.id, section);
    showDialog(
      `<form id="roster-import-form">${dialogHeader("Import roster")}<div class="dialog-body"><p class="roster-import-lead">Adds new students and updates existing ones. Nobody is removed. Students the registrar marks as a Q drop or withdrawal are dropped; students the file leaves out are listed on the roster for you to drop.</p><p class="roster-import-target"><span>Importing into</span><strong>${escape(group.code)} · ${escape(courseTerm(group))} · Section ${section}</strong></p><label class="field">Roster export<input type="file" name="file" accept="${ACCEPT}" required autofocus aria-describedby="roster-upload-hint"></label><p id="roster-upload-hint" class="field-hint">CSV or Excel (.xlsx), up to 10 MB. Every column is kept.</p>${idsDisclosure()}<p id="form-error" class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Import roster</button></footer></form>`,
    );
    $("#roster-import-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const file = form.elements.file.files[0];
      if (!validFile(file)) {
        $("#form-error").textContent =
          "Choose a CSV or XLSX file of 10 MB or less.";
        $("#form-error").hidden = false;
        return;
      }
      const data = new FormData(form);
      await saveForm(
        form,
        () => api(route, "POST", data),
        "Roster imported.",
        () => { void loadRosterPanel(); refreshCounts(true); },
      );
    });
  }

  // Several sections at once: each file's section is suggested from its name (sectionFromFileName) and stays the instructor's
  // to change; files without one are not sent. Each file goes to the existing per-section import, one at a time, with its own result.
  // IMPORT FROM HOWDY (CANVAS, 2026-11-01): Import from Howdy signs in to Howdy (aim-signin.js) and comes back here with each section's
  // roster as a file (`fromHowdy`: {files: {section: File}, problems}), its section chosen, and imports them at once.
  function importRostersDialog(fromHowdy = null) {
    const group = selectedCourseGroup();
    if (!group || !group.sections.length) return;
    showDialog(
      `<form id="roster-multi-form">${dialogHeader("Import rosters", `${escape(group.code)} · ${escape(courseTerm(group))}`)}<div class="dialog-body"><p class="roster-import-lead">Adds new students and updates existing ones. Nobody is removed. Students the registrar marks as a Q drop or withdrawal are dropped; students the file leaves out are listed on the roster for you to drop.</p>${fromHowdy ? "" : `<div class="roster-howdy"><button type="button" class="button" data-roster-howdy>Import from Howdy</button><p class="field-hint">Signs in to Howdy with your NetID and Duo and imports every section’s class roster. Or choose the files below.</p></div>`}<label class="field"${fromHowdy ? " hidden" : ""}>Roster exports<input type="file" name="files" multiple accept="${ACCEPT}" aria-describedby="roster-multi-hint"></label><p id="roster-multi-hint" class="field-hint"${fromHowdy ? " hidden" : ""}>Choose one CSV or Excel file per section. Each file’s section is picked from its name when the name says it; check them before importing. A file without a section is not sent.</p><ol class="roster-multi-list" data-roster-files></ol>${idsDisclosure()}<p id="form-error" class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Import rosters</button></footer></form>`,
    );
    const form = $("#roster-multi-form");
    const list = form.querySelector("[data-roster-files]");
    const error = form.querySelector("#form-error");
    let files = [];
    /** @param {File[]} chosen @param {(file: File) => string} sectionOf */
    const draw = (chosen, sectionOf) => {
      files = chosen;
      error.hidden = true;
      list.innerHTML = files.map((file, index) => `<li data-roster-file="${index}"><span class="roster-multi-file"><strong>${escape(file.name)}</strong><small>${Math.max(1, Math.round(file.size / 1024)).toLocaleString()} KB</small></span><label class="field">Section<select data-roster-section aria-label="Section for ${escape(file.name)}"><option value="">Choose a section…</option>${group.sections.map((section) => `<option value="${section}"${String(section) === sectionOf(file) ? " selected" : ""}>Section ${section}</option>`).join("")}</select></label><p class="roster-multi-result" data-roster-result role="status"></p></li>`).join("");
    };
    form.elements.files.addEventListener("change", () => draw([...form.elements.files.files], (file) => sectionFromFileName(file.name, group.sections)));
    form.querySelector("[data-roster-howdy]")?.addEventListener("click", () => {
      void openHowdyRosters({ api, showDialog, dialogHeader, escape,
        start: { route: `/api/course-groups/${encodeURIComponent(group.id)}/howdy-rosters`, body: {} },
        onExported: (result) => importRostersDialog({ files: result.files, problems: result.problems }) });
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (form.dataset.busy === "true") return;
      const rows = [...list.querySelectorAll("[data-roster-file]")];
      const choices = rows.map((row) => row.querySelector("[data-roster-section]").value);
      const fail = (message) => { error.textContent = message; error.hidden = false; };
      if (!rows.length) return fail("Choose the roster files to import.");
      if (!choices.some(Boolean)) return fail("Choose a section for at least one file.");
      const duplicate = duplicateSection(choices);
      if (duplicate) return fail(`Section ${duplicate} is chosen for more than one file. Choose one file per section.`);
      error.hidden = true;
      form.dataset.busy = "true";
      const controls = [...form.querySelectorAll("input, select, button[type=submit]")];
      controls.forEach((control) => { control.disabled = true; });
      let imported = 0;
      for (const [index, row] of rows.entries()) {
        const result = row.querySelector("[data-roster-result]");
        const file = files[Number(row.dataset.rosterFile)];
        const section = choices[index];
        row.dataset.state = "";
        if (!section) { result.textContent = "Not sent: no section chosen."; row.dataset.state = "skipped"; continue; }
        if (!validFile(file)) { result.textContent = "Not sent: choose a CSV or XLSX file of 10 MB or less."; row.dataset.state = "failed"; continue; }
        result.textContent = "Importing…";
        const body = new FormData();
        body.append("file", file, file.name);
        try {
          const saved = await api(rosterRoute(group.id, Number(section)), "POST", body);
          const latest = saved.last_import || {};
          result.textContent = `Section ${section}: ${saved.count} students · ${latest.added ?? 0} added · ${latest.updated ?? 0} updated.`;
          // Q drops and withdrawals the import dropped (last_import.registrar_dropped) are named here.
          if (latest.registrar_dropped?.length) {
            const note = document.createElement("span");
            note.className = "roster-multi-leaving";
            note.dataset.rosterRegistrarDropped = "";
            note.textContent = ` Dropped: ${latest.registrar_dropped.map((row) => `${row.name} (${row.reason})`).join("; ")}.`;
            result.append(note);
          }
          // Students the file left out (rosters.leaving) are named here, with the way to the roster's Drop.
          if (saved.leaving?.length) {
            const note = document.createElement("span");
            note.className = "roster-multi-leaving";
            note.innerHTML = ` ${escape(leavingText(saved.leaving))} <a class="link-arrow" href="${rosterHref(group, Number(section))}" data-roster-leaving-link>Review on the roster</a>`;
            note.querySelector("a").addEventListener("click", () => $("#dialog").close());
            result.append(note);
          }
          row.dataset.state = "done";
          imported++;
        } catch (cause) {
          result.textContent = `Section ${section}: ${cause.message}`;
          row.dataset.state = "failed";
        }
      }
      form.dataset.busy = "false";
      form.querySelector('.dialog-footer [data-action="close"]').textContent = "Close";
      form.querySelector('button[type="submit"]').hidden = true;
      if (imported) {
        toast(`${imported} roster${imported === 1 ? "" : "s"} imported.`);
        refreshCounts(true);
      }
    });
    if (fromHowdy) {
      const sections = new Map(Object.entries(fromHowdy.files).map(([section, file]) => [file, section]));
      draw([...sections.keys()], (file) => sections.get(file) || "");
      if (fromHowdy.problems.length) {
        const note = document.createElement("p");
        note.className = "field-hint";
        note.dataset.rosterHowdyProblems = "";
        note.textContent = fromHowdy.problems.join(" ");
        list.after(note);
      }
      if (files.length) form.requestSubmit();
      else { error.textContent = "Howdy gave no roster to import."; error.hidden = false; }
    }
  }

  // Start next term: the Add a course dialog, filled with this course's code, name and section numbers. Term and year stay
  // empty for the instructor; every check is the dialog's own. The Previous course list keeps its own "name and code" rule.
  function startNextTerm() {
    const group = selectedCourseGroup();
    if (!group) return;
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.hidden = true;
    trigger.dataset.action = "add-course";
    document.body.append(trigger);
    trigger.click();
    trigger.remove();
    const form = $("#course-form");
    if (!form) return;
    const previous = form.elements.previous_course;
    const option = previous && [...previous.options].find((item) => item.textContent.startsWith(`${group.code} —`));
    if (option) {
      previous.value = option.value;
      previous.dispatchEvent(new Event("change", { bubbles: true }));
    }
    form.elements.name.value = group.name;
    const sections = group.sections.map(String);
    if (sections.length > 1) {
      form.elements.multiple_sections.checked = true;
      form.elements.multiple_sections.dispatchEvent(new Event("change", { bubbles: true }));
      for (let index = form.querySelectorAll('[name="section"]').length; index < sections.length; index++) form.querySelector("#add-section")?.click();
    }
    form.querySelectorAll('[name="section"]').forEach((input, index) => {
      input.value = sections[index] ?? "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    form.elements.term?.focus();
  }

  function updateScopeToggle() {
    // The page's own Current | All switch (when shown): counts and the selected scope.
    document.querySelectorAll("[data-course-scope]").forEach((link) => {
      const selected = link.dataset.courseScope === state.view;
      link.classList.toggle("active", selected);
      if (selected) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    document.querySelectorAll("[data-course-count]").forEach((node) => {
      node.textContent = node.dataset.courseCount === "current"
        ? state.courseGroups.filter((course) => course.is_current).length
        : state.courseGroups.length;
    });
  }

  $("#course-search").addEventListener("input", (event) => {
    state.query = event.target.value;
    if (state.loaded) renderCourses();
  });
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    if (event.target.closest("#course-detail [data-course-import-rosters]")) importRostersDialog();
    else if (event.target.closest("#course-detail [data-course-next-term]")) startNextTerm();
    else if (event.target.closest("#course-detail [data-course-canvas-sync]")) void syncGrades(event.target.closest("[data-course-canvas-sync]"));
    else if (event.target.closest("#course-detail [data-grading-scheme]")) void editGradingScheme(event.target.closest("[data-grading-scheme]"));
  });
  // Arrow keys, Home and End move between the course page's tabs (the ARIA tabs pattern).
  document.addEventListener("keydown", (event) => {
    const tab = event.target.closest?.('#course-detail [role="tab"]');
    if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = [...document.querySelectorAll('#course-detail [role="tab"]')];
    const index = tabs.indexOf(tab);
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? tabs.length - 1
          : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
            tabs.length;
    event.preventDefault();
    tabs[next].focus({ preventScroll: true });
    location.hash = tabs[next].hash;
  });

  return {
    isCourseView, selectedCourseGroup, courseHref, renderCourses, renderCourseDetail, storageSummary,
    loadRosterPanel, importRosterDialog,
  };
}
