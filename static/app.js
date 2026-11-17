import { icon, mountIcons } from "./icons.js";
import { createHome } from "./home.js";
import { mountWorkspaceNavigation } from "./workspace-navigation.js";
import { mountTitleFit } from "./page-title-fit.js";
import { createCalendar } from "./calendar.js";
import { createMail } from "./mail.js";
import { createCommunications } from "./communications.js";
import { createCanvasHub } from "./canvas-hub.js";
import { openAimUpload, openCanvasSignIn } from "./aim-signin.js";
import { createAssistant } from "./assistant.js";
import { createAreaAssistant } from "./assistant-area.js";
import { createFileViewer } from "./file-viewer.js";
import { createApiClient } from "./request.js";
import { createInbox } from "./inbox.js";
import { createDocuments } from "./documents.js";
import { createAssignmentWorkspace } from "./assignment-workspace.js";
import { createRooms } from "./rooms.js";
import { createSeating } from "./seating.js";
import { createResponseCards } from "./response-cards.js";
import { createExams } from "./exams.js";
import { createExamDay } from "./exam-day.js";
import { createGrading } from "./grading.js";
import { createGradebook } from "./gradebook.js";
import { createCanvasContent } from "./canvas-content.js";
import { createCourseNotes } from "./course-notes.js";
import { createTermSchedule } from "./term-schedule.js";
import { createClassSchedule } from "./class-schedule.js";
import { createQuestionBank } from "./question-bank.js";
import { createRubricMemories } from "./rubric-memories.js";
import { createMemorySettings } from "./memory-settings.js";
import { createRecordings } from "./recordings.js";
import { importAccommodations } from "./accommodations.js";
import { createCourses, courseTerm, storagePending } from "./courses.js";
import { installLeaveGuard, registerShortcut, listShortcuts, rememberDisclosures, bindActionMenu, toastSpec, disclosureMarkup, setBusy, dialogSession, runFormAction, escape } from "./ui.js";
import { createAccommodations } from "./accommodations_page.js";
import { createAbsences } from "./absences.js";
import { createStudentNotes } from "./student-notes.js";
import { createTeachingReview } from "./teaching-review.js";
import { createStudentSupport } from "./student-support.js";
import { createHeader, routePath, attentionBadges, assignmentDecisions, navEntry, ASSIGNMENT_BADGES, BADGE_ROUTES } from "./page-header.js";
import { mountSystemStatus } from "./system-status.js";
import { createPalette, rememberPlace } from "./palette.js";

// Before any view listens to hashchange: unsaved work registered with registerLeaveGuard can keep the page.
installLeaveGuard();
rememberDisclosures(document);
bindActionMenu(document);

const $ = (selector) => document.querySelector(selector);
const statusNames = {
  planned: "Planned",
  building: "Building",
  ready: "Ready",
};
const categories = [
  "Administration",
  "Teaching",
  "Assessment",
  "Resources",
  "Other",
];
const icons = [
  "users",
  "book",
  "check",
  "folder",
  "calendar",
  "spark",
  "grid",
  "chart",
];
const colors = ["sage", "clay", "blue", "lavender", "sand"];
const terms = [
  "Fall",
  "Spring",
  "Summer I",
  "Summer II",
  "Summer (Full)",
  "Other",
];
const state = {
  tools: [],
  courses: [],
  courseGroups: [],
  courseId: "",
  courseSection: null,
  courseBackView: "all-courses",
  coursePrefixes: ["MATH"],
  rooms: [],
  roomId: "",
  seatingCharts: [],
  seatingId: "",
  storage: { enabled: false },
  counts: { rooms: 0, seating: 0 },
  settings: { workspace_name: "My teaching workspace", teacher_name: "" },
  view: "overview",
  filter: "all",
  query: "",
  sort: "pinned",
  loaded: false,
};
// Toast v2's stack (see toast() below); declared first because views may toast while they are created.
const TOAST_LIMIT = 3;
const toasts = [];
// The stack element, kept even while it lives inside a modal dialog (raiseToasts below).
let toastHost = null, toastWatch = null;
function toastStack() { return (toastHost ||= $("#toast")); }
const api = createApiClient({ onConflict: () => { void refresh(true); } });
// COORDINATOR: the hint under the course card's Coordinated course box.
const COORDINATOR_HINT = "For this course’s term. The coordinator’s email comes to Follow-up as theirs, not a student’s.";
// The page header: the views table below gives every route its defaults; views name their object with header.set.
// The header band's title fits its line after every header paint (page-title-fit.js).
const titleFit = mountTitleFit();
const header = createHeader({ icon, onPaint: () => titleFit.fit() });
// Home's projection, fetched once per shell refresh for Home and the sidebar's attention badges.
const overviewStore = createOverviewStore();
// The course pages (list, course/term/section workspace, roster). Created first: Home links into them with courseHref.
const {
  isCourseView, selectedCourseGroup, courseHref, renderCourses, renderCourseDetail, storageSummary,
  loadRosterPanel, importRosterDialog,
} = createCourses({
  api, state, escape, icon, toast, showDialog, dialogHeader, saveForm, header,
  courseResources: (group, section) => inboxView.courseResources(group, section),
  notesSummary: (group, node) => courseNotesView.summary(group, node),
  trainingsSummary: (group, node) => recordingsView.summary(group, node),
  bankSummary: (group, node) => questionBankView.summary(group, node),
  memoriesSummary: (group, node) => rubricMemoriesView.summary(group, node),
  studentMail: (info) => mailView.openStudentMail(info),
  compose: (prefill) => mailView.openCompose(prefill),
  // A student's communications record (communications.js): the timeline, and its newest entries in the student window.
  communications: (info) => communicationsView.openTimeline(info),
  communicationsSummary: (host, enrollmentId, open) => communicationsView.summary(host, enrollmentId, open),
  absencesPanel: (host, who) => absencesView.panel(host, who),
  studentNotesPanel: (host, who) => studentNotesView.panel(host, who),
  studentSupport: (who) => studentSupportView.openForStudent(who),
  // The course Home's Sync grades: Sync with Canvas for every section of the term (gradebook.js openTermSync).
  canvasSync: (target) => gradebookView.openTermSync(target),
  // The course Home's Grading scheme: the term's letter scale for every section (gradebook.js openTermScheme).
  gradingScheme: (target) => gradebookView.openTermScheme(target),
  // One Courses entry in the sidebar; the page's own Current | All switch carries the two lists and their counts.
  courseToggle: true,
});
const homeView = createHome({api, escape, icon, courseHref, overviewStore, header, toast, ask: (...args) => assistantView.ask(...args)});
let shellSignature = "";
// The last /api/state read: null before the first, then whether the server answered.
let online = null;
let systemStatus = { update() {} };
let badgeTimer = 0;
let refreshing = null;
let dialogTool = null;
let dialogCourse = null;
let assignmentContext = false; // The assignment shell's context is on screen (`faculty:assignment-context`; highlightNav).
const assignmentsView = createAssignmentWorkspace({api, escape, header, toast, showDialog, dialogHeader, documents: () => documentsView, overviewStore, ask: (...args) => assistantView.ask(...args)});
const assignmentHeader = assignmentsView.editorHeader;
const inboxView = createInbox({
  api,
  icon,
  escape,
  showDialog,
  dialogHeader,
  toast,
  header: assignmentHeader,
  overviewStore,
});

const documentsView = createDocuments({ api, escape, toast, showDialog, dialogHeader, header: assignmentHeader, getSeating: () => seatingView, refresh: (force) => refresh(force) });
// A student's name opens their gradebook record (gradebook.js openStudentRecord), the card a name opens in the section's gradebook.
const accommodationsView = createAccommodations({ api, escape, toast, showDialog, dialogHeader, header,
  openStudentRecord: (target) => gradebookView.openStudentRecord(target) });
const responseCardsView = createResponseCards({ api, escape, toast, showDialog, dialogHeader, header });
const examsView = createExams({ api, escape, icon, toast, showDialog, dialogHeader, header: assignmentHeader });
// An assessment's exam-day plan (exam-day.js, AGENTS.md "Exam-day packet"): `#exam-day/<assignment>/<term>`, linked from the Exams
// hub's Exam day stage; each room's packet prints from its seating chart.
const examDayView = createExamDay({ api, escape, icon, toast, header: assignmentHeader });
const gradingView = createGrading({ api, escape, icon, toast, showDialog, dialogHeader, header: assignmentHeader, overviewStore, ask: (...args) => assistantView.ask(...args) });
const gradebookView = createGradebook({ api, escape, icon, toast, showDialog, dialogHeader, header: assignmentHeader,
  // The record's timeline searches the student's Gmail and writes to them through Mail (`mail`: whom, read from the roster).
  communications: ({ mail = null, ...info }) => communicationsView.openTimeline(mail ? { ...info, onMail: () => mailView.openStudentMail(mail),
    onWrite: () => mailView.openStudentMail({ ...mail, write: true }) } : info), absences: () => absencesView, notes: () => studentNotesView,
  studentSupport: (who) => studentSupportView.openForStudent(who) });
// Excused absences (AGENTS.md "Excused absences"): #absences, and the Absences part of a student's card (gradebook, roster).
const absencesView = createAbsences({ api, escape, toast, showDialog, dialogHeader, header });
// Student notes (AGENTS.md "Student notes"): your own notes about a student, on their card (gradebook, roster) and from the palette.
const studentNotesView = createStudentNotes({ api, escape, toast, showDialog, dialogHeader });
const canvasContentView = createCanvasContent({ api, escape, icon, toast, showDialog, dialogHeader, ask: (...args) => assistantView.ask(...args) });
// A course's lecture notes (course-notes.js): `#course/<group>/home/notes`, uploaded once and split into their sections.
const courseNotesView = createCourseNotes({ api, escape, icon, toast, showDialog, dialogHeader, header });
const termScheduleView = createTermSchedule({ api, escape, icon, toast, header, showDialog, dialogHeader });
// A section's Class schedule (class-schedule.js): `#course/<group>/section/<n>/schedule`, its class days, topics and dated work.
const classScheduleView = createClassSchedule({ api, escape, toast, header, showDialog, dialogHeader });
// A course's question bank (question-bank.js): `#course/<group>/home/questions`, its outline following the lecture notes.
const questionBankView = createQuestionBank({ api, escape, icon, toast, showDialog, dialogHeader, header });
// A course's rubric memories (rubric-memories.js, GRADING "RUBRIC MEMORIES"): what the rubric drafter and the rubric check follow,
// kept or dropped by you. Drawn inside the Memory page, which has its own header (so none is passed here); a course Home's line
// counts them and links there.
const rubricMemoriesView = createRubricMemories({ api, escape, toast, showDialog, dialogHeader });
// The Memory page (memory-settings.js, ASSISTANT-MEMORY "ONE PAGE"): `#memory`, opened from Workspace settings. Every memory
// Faculty keeps and how it learns, in one place.
const memoryView = createMemorySettings({ api, toast, rubricMemories: rubricMemoriesView, groups: () => state.courseGroups });
// Recordings (recordings.js, RECORDINGS step 1): `#recordings`, what the Plaud recorder holds and what each recording is filed
// under; opened from the rail (TODAY, after Follow-up, since 2026-11-14), the palette's Pages and Workspace settings.
// `changed`: Keep, Don't keep and Ask me again change what waits for the instructor, which Home's card and the rail count.
const recordingsView = createRecordings({ api, escape, toast, showDialog, dialogHeader, header, changed: () => overviewStore.refresh() });
const calendarView = createCalendar({ api, escape, icon, toast, showDialog, dialogHeader, header });
// A student's name on a message opens their gradebook record, as on the Accommodations page.
const mailView = createMail({ api, escape, toast, showDialog, dialogHeader, header,
  openStudentRecord: (target) => gradebookView.openStudentRecord(target),
  draftAbsence: (target) => absencesView.draftFromMail(target),
  // A reply that goes by email only because Faculty is signed out of Canvas offers Canvas's sign-in window (MAIL-CANVAS-POST).
  canvasSignIn: () => openCanvasSignIn({ api, showDialog, dialogHeader, escape }) });
// A student's communications record (AGENTS.md "Communications record"): opened from the roster's student window and the gradebook.
const communicationsView = createCommunications({ api, escape, toast, showDialog, dialogHeader,
  studentSupport: (who) => studentSupportView.openForStudent(who) });
const studentSupportView = createStudentSupport({ api, escape, toast, showDialog, dialogHeader, header,
  draftAbsence: (target) => absencesView.draftFromMail(target),
  openStudent: (who) => gradebookView.openStudentRecord(who),
  communications: (who) => communicationsView.openTimeline(who),
  notes: (prefill) => studentNotesView.openNew(prefill),
  writeStudent: async ({termId, sectionId, enrollmentId, onSent, reply = null}) => {
    const view = await api(`/api/gradebook/terms/${encodeURIComponent(termId)}?section=${encodeURIComponent(sectionId)}`);
    const student = view.students.find(s => s.row === `${sectionId}:${enrollmentId}`);
    if (!student) throw new Error("This student is no longer in that section.");
    return mailView.openStudentMail({group: view.term.group_id, section: student.section, studentId: enrollmentId, name: student.name, write: true, onSent, reply});
  }, changed: () => overviewStore.refresh(),
  // Edit in Mail (ASSISTANT-FOLLOWUP-SUGGESTIONS): a suggested reply opens in Mail's own compose window; Send stays there.
  editMail: (prefill) => mailView.compose(prefill) });
const canvasHubView = createCanvasHub({ api, escape, header, toast, showDialog, dialogHeader });
// The Faculty assistant's drawer (AGENTS.md "Faculty assistant"): opened from the top bar, Ctrl/⌘ I or the palette.
// Its chat moves to the floating window and back (ASSISTANT "BOTH WAYS"): each surface is given the other's way in.
const assistantView = createAssistant({ api, icon, toast, registerShortcut, showDialog, dialogHeader, editMail: (prefill) => mailView.compose(prefill),
  openWindow: (id, draft) => areaAssistantView.adopt(id, draft) });
// Files the assistant finds (scans, crops, papers, PDFs) and mail attachments open in the app with Download, not as downloads.
const fileViewer = createFileViewer({ api, escape, icon });
// Print at Home on a print file opened in the viewer (DOCUMENTS "SEND TO PRINTER"); its Back closes that window and reopens the file.
fileViewer.setPrintAtHome((target, back) => documentsView.printAtHome(target, () => { $("#dialog").close(); back(); }));
const areaAssistantView = createAreaAssistant({api,icon,toast,registerShortcut,prepareSelection: () => fileViewer.prepareSelection(),openConversation: (id,parent,release) => assistantView.openConversation(id,parent,release)});
const teachingReviewView = createTeachingReview({api, escape, toast, header: assignmentHeader, ask: message => assistantView.ask(message), showDialog, dialogHeader});
const seatingView = createSeating({
  api,
  icon,
  escape,
  toast,
  showDialog,
  dialogHeader,
  header: assignmentHeader,
  getCharts: () => state.seatingCharts,
  refresh,
});
const roomsView = createRooms({
  api,
  icon,
  escape,
  toast,
  showDialog,
  dialogHeader,
  header,
  getRooms: () => state.rooms,
  refresh,
});

/**
 * GET /api/workspace/overview, shared: the shell refreshes it with its own poll (30 s, focus, visibility) and Home and the
 * sidebar badges subscribe, so the projection is read once per refresh wherever the instructor is.
 */
function createOverviewStore() {
  let data = null;
  let error = null;
  let pending = null;
  let readAt = 0;
  const listeners = new Set();
  const emit = () => listeners.forEach((listener) => {
    try { listener(data, error); } catch (cause) { console.error(cause); }
  });
  return {
    get: () => data,
    error: () => error,
    /** When the projection on hand was read (ms), for 'Updated … ago'. */
    at: () => (data ? readAt : 0),
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    /** Read it again; `maxAge` (ms) lets a page that does not show it reuse a recent read. */
    refresh({ maxAge = 0 } = {}) {
      if (pending) return pending;
      if (maxAge && data && Date.now() - readAt < maxAge) return Promise.resolve(data);
      readAt = Date.now();
      pending = api("/api/workspace/overview")
        .then((result) => { data = result; error = null; })
        .catch((cause) => { error = cause; })
        .then(() => { emit(); return data; })
        .finally(() => { pending = null; });
      return pending;
    },
  };
}

// Sidebar attention badges and the '(N)' in the tab title: derived from the server's attention cards, never decided here.
overviewStore.subscribe((data) => {
  if (!data) return;
  const { counts, health, total } = attentionBadges(data.attention || []);
  // The Assignments badge counts decisions, one per needs-you item, as the assignment tabs and the index do; never the responses
  // or questions inside them (2026-10-18). The tab title's '(N)' counts the same, so it agrees with the badges it adds up.
  const summed = ASSIGNMENT_BADGES.reduce((sum, view) => sum + (counts[view] || 0), 0);
  counts.assignments = assignmentDecisions(data.attention || []);
  for (const [view] of [...BADGE_ROUTES, ['assignments']]) {
    const badge = document.getElementById(`nav-badge-${view}`);
    const link = document.querySelector(`.sidebar nav [data-view="${view}"]`);
    if (!badge || !link) continue;
    const count = counts[view] || 0;
    const markup = count ? `${count.toLocaleString()}<span class="sr-only"> need${count === 1 ? "s" : ""} attention</span>` : "";
    if (badge.innerHTML !== markup) badge.innerHTML = markup;
    badge.hidden = !count;
    if (count) link.setAttribute("aria-describedby", badge.id);
    else link.removeAttribute("aria-describedby");
  }
  const dot = document.getElementById("nav-health");
  const home = document.querySelector('.sidebar nav [data-view="overview"]');
  if (dot && home) {
    dot.hidden = !health;
    if (health) home.setAttribute("aria-describedby", dot.id);
    else home.removeAttribute("aria-describedby");
  }
  header.setTitleParts({ count: total - summed + counts.assignments });
});

/**
 * Toast v2: toast("Saved"), toast("Failed", true), toast("Deleted", {tone: "success", action: {label: "Restore", run}}).
 * Up to three stack in #toast. Success and information hide after a few seconds; errors are alerts that stay until dismissed
 * or until the page (the route's path) changes. The same message again restarts its timer instead of stacking a copy.
 */
function toast(message, errorOrOptions = false) {
  const spec = toastSpec(message, errorOrOptions);
  const host = toastStack();
  if (!host || !spec.message) return;
  let entry = toasts.find((item) => item.spec.message === spec.message && item.spec.tone === spec.tone && !item.spec.action && !spec.action);
  if (entry) {
    clearTimeout(entry.timer);
    host.append(entry.node);
    toasts.splice(toasts.indexOf(entry), 1);
    toasts.push(entry);
  } else {
    const node = document.createElement("div");
    node.className = "toast-item";
    node.dataset.tone = spec.tone;
    if (spec.role === "alert") node.setAttribute("role", "alert");
    node.innerHTML = `<span class="toast-message">${escape(spec.message)}</span>${spec.action ? `<button type="button" class="toast-action">${escape(spec.action.label)}</button>` : ""}${spec.persist || spec.action ? `<button type="button" class="toast-close" aria-label="Dismiss">${icon("close")}</button>` : ""}`;
    entry = { spec, node, timer: 0, path: routePath(location.hash) };
    node.querySelector(".toast-close")?.addEventListener("click", () => dismissToast(entry));
    node.querySelector(".toast-action")?.addEventListener("click", async (event) => {
      event.currentTarget.disabled = true;
      dismissToast(entry);
      try { await spec.action.run(); } catch (error) { toast(error?.message || "That did not work. Please try again.", true); }
    });
    toasts.push(entry);
    host.append(node);
    while (toasts.length > TOAST_LIMIT) dismissToast(toasts[0]);
  }
  host.hidden = false;
  raiseToasts(host);
  if (!spec.persist) entry.timer = setTimeout(() => dismissToast(entry), spec.timeout);
}

// FRONTEND: toasts stay reachable above whatever is open. The stack is a manual popover shown again on each toast, so it sits
// above the Assistant (drawer re-hosted in a dialog, floating chat) in the top layer. Everything outside an open modal dialog is
// inert, so while one is open the stack lives inside it, put back if the dialog's content is rewritten, and returns to the body
// when the dialog closes. Only the toasts' own buttons take clicks (components.css), so a toast never blocks a dialog's controls.
function showToastStack(host) {
  try { if (host.matches(":popover-open")) host.hidePopover(); host.showPopover(); } catch { /* an older browser keeps the fixed stack */ }
}
function rehomeToasts(host) {
  toastWatch?.disconnect(); toastWatch = null;
  document.body.append(host);
  if (!host.hidden) raiseToasts(host);
}
function raiseToasts(host) {
  if (!host.showPopover) return;
  if (!host.hasAttribute("popover")) host.setAttribute("popover", "manual");
  const modal = [...document.querySelectorAll("dialog[open]")].reverse().find((dialog) => { try { return dialog.matches(":modal"); } catch { return false; } });
  const home = modal || document.body;
  if (host.parentElement !== home) {
    toastWatch?.disconnect(); toastWatch = null;
    home.append(host);
    if (modal) {
      toastWatch = new MutationObserver(() => {
        if (!modal.open || !modal.isConnected) { rehomeToasts(host); return; }
        if (host.parentElement !== modal) { modal.append(host); if (!host.hidden) showToastStack(host); }
      });
      toastWatch.observe(modal, { childList: true });
      modal.addEventListener("close", () => { if (host.parentElement === modal || !host.isConnected) rehomeToasts(host); }, { once: true });
    }
  }
  // Shown again only when something else may now be above it (re-showing replays the toasts' entrance).
  const open = (() => { try { return host.matches(":popover-open"); } catch { return false; } })();
  if (open && ![...document.querySelectorAll("dialog[open], :popover-open")].some((node) => node !== host)) return;
  showToastStack(host);
}

function dismissToast(entry) {
  const index = toasts.indexOf(entry);
  if (index === -1) return;
  clearTimeout(entry.timer);
  toasts.splice(index, 1);
  entry.node.remove();
  if (!toasts.length) { const host = toastStack(); host.hidden = true; try { host.hidePopover?.(); } catch { /* not shown as a popover */ } }
}

// Errors belong to the page they happened on.
function dismissPageToasts(path) {
  toasts.filter((entry) => entry.spec.persist && entry.path !== path).forEach(dismissToast);
}

async function refresh(afterMutation = false) {
  if (refreshing) {
    const result = await refreshing;
    // A read already in flight may predate the edit that just finished.
    return afterMutation ? refresh(true) : result;
  }
  refreshing = (async () => {
    try {
      const include = state.view === "rooms" ? "core,rooms" : state.view === "seating" ? "core,seating" : isCourseView() || state.view === "course" ? "core,storage" : "core";
      const data = await api(`/api/state?include=${include}`);
      state.tools = data.tools;
      state.courses = data.courses;
      state.courseGroups = data.course_groups;
      state.coursePrefixes = data.course_prefixes;
      if (data.rooms) state.rooms = data.rooms;
      if (data.seating_charts) state.seatingCharts = data.seating_charts;
      if (data.storage) state.storage = data.storage;
      state.counts = data.counts;
      state.settings = data.settings;
      state.loaded = true;
      $("#connection-error").hidden = true;
      $(".server-status").className = "server-status connected";
      online = true;
      systemStatus.update();
      const signature = `${location.hash}:${JSON.stringify(data)}`;
      const simpleView = ["overview", "tools", "pinned", "roadmap", "all-courses", "current-courses"].includes(state.view);
      if (!simpleView || signature !== shellSignature) render();
      shellSignature = signature;
      // Home's projection and the sidebar badges (Home renders from the same read when it is on screen). Elsewhere the
      // badges wait until the page's own reads are done, and one read serves each 30 s poll.
      clearTimeout(badgeTimer);
      if (state.view === "overview") void overviewStore.refresh();
      else badgeTimer = setTimeout(() => { if (!document.hidden) void overviewStore.refresh({ maxAge: 25000 }); }, 1500);
      if (state.view === "calendar") void calendarView.refresh();
      if (state.view === "mail") void mailView.refresh();
      void loadRosterPanel();
      return true;
    } catch {
      $("#connection-error").hidden = false;
      $(".server-status").className = "server-status offline";
      online = false;
      systemStatus.update();
      if (!state.loaded)
        $(
          state.view === "course"
            ? "#course-detail"
            : isCourseView()
              ? "#courses-grid"
              : "#tools-grid",
        ).innerHTML =
          '<div class="empty-state">' +
          icon("server") +
          '<h3>Your workspace is out of reach.</h3><p>Check that the home server is running, then reconnect.</p><button class="button" data-action="refresh">Try again</button></div>';
      return false;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

function toolCard(tool) {
  const editableName = escape(tool.name);
  return `<article class="tool-card" data-tool-id="${tool.id}">
    <div class="tool-card-top"><span class="tool-icon ${tool.color}">${icon(tool.icon)}</span><button class="icon-button ${tool.pinned ? "is-pinned" : ""}" data-action="pin" data-id="${tool.id}" aria-label="${tool.pinned ? "Unpin" : "Pin"} ${editableName}" aria-pressed="${tool.pinned}" title="${tool.pinned ? "Unpin tool" : "Pin tool"}">${icon("pin")}</button><button class="icon-button" data-action="edit" data-id="${tool.id}" aria-label="Edit ${editableName}" title="Edit tool">${icon("edit")}</button></div>
    <span class="category">${escape(tool.category)}</span><h3>${editableName}</h3><p class="tool-description">${escape(tool.description) || "A new part of your teaching workspace."}</p>
    <div class="tool-card-bottom"><span class="badge ${tool.status}"><span class="status-dot ${tool.status}"></span>${statusNames[tool.status]}</span>${tool.url ? `<a class="text-button" href="${escape(tool.url)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${editableName} in a new tab">Open tool ${icon("arrow-up-right")}</a>` : `<button class="text-button" data-action="edit" data-id="${tool.id}">Set up tool ${icon("arrow-right")}</button>`}</div>
  </article>`;
}

/* The views table: every route's header defaults (title, subtitle, trail, variant and primary action), ported unchanged
   from the former per-route if-chain. Views name their object with header.set({route, ...}); these stay the fallback. */
const SECTIONS = {
  assignments: ["Assignments", "#assignments"],
  overview: ["Home", "#overview"],
  calendar: ["Calendar", "#calendar"],
  mail: ["Mail", "#mail"],
  "teaching-review": ["Teaching review", "#teaching-review"],
  "grade-delivery": ["Grade delivery", "#grade-delivery"],
  "follow-ups": ["Student follow-up", "#follow-ups"],
  "office-hours": ["Office hours", "#office-hours"],
  canvas: ["Canvas", "#canvas"],
  "all-courses": ["All courses", "#all-courses"],
  "current-courses": ["Current courses", "#current-courses"],
  accommodations: ["Accommodations", "#accommodations"],
  absences: ["Absences", "#absences"],
  memory: ["Memory", "#memory"],
  recordings: ["Recordings", "#recordings"],
  documents: ["Assignments", "#documents"],
  seating: ["Seating charts", "#seating"],
  "response-cards": ["Response Cards", "#response-cards"],
  inbox: ["Teaching Inbox", "#inbox"],
  exams: ["Exams", "#exams"],
  "exam-day": ["Exams", "#exams"],
  grading: ["Grading", "#grading"],
  rooms: ["Rooms", "#rooms"],
  tools: ["Tools", "#tools"],
  pinned: ["Pinned tools", "#pinned"],
  roadmap: ["Build roadmap", "#roadmap"],
};
// The last crumb of a detail route until its view names the object.
const DETAIL_LABELS = [
  [/^inbox\/job\//, "Scan"],
  [/^exams\/assessment\//, "Assessment"],
  [/^exams\//, "Exam"],
  [/^exam-day\//, "Exam-day packet"],
  [/^grading\//, "Assignment"],
  [/^gradebook\/[^/]+\/setup/, "Gradebook setup"],
  [/^gradebook\//, "Term gradebook"],
  [/^response-cards\/issue\//, "Issue"],
  [/^response-cards\/template\//, "Template"],
  [/^response-cards\//, "Response Cards"],
  [/^recordings\//, "Recording"],
];
const add = (label, action) => ({ label, action, icon: "plus" });
const VIEWS = {
  assignments: () => ({title: "Assignments", subtitle: "From the first draft to returned grades.", action: null}),
  overview: ({ teacher }) => ({
    variant: "hero",
    title: teacher ? `Welcome back, ${teacher}.` : "Your teaching day.",
    subtitle: "Your courses, upcoming exams, and work that needs attention.",
    action: add("Upload scanned assignment", "inbox-upload-scans"),
  }),
  calendar: () => ({ title: "Calendar", subtitle: "", action: add("Add event", "add-calendar-event") }),
  mail: () => ({ title: "Mail", subtitle: "Read your Gmail, see a student’s mail, and write from Faculty.", action: null }),
  "teaching-review": () => ({title: "Teaching review", subtitle: "Certified evidence to guide your next lesson.", action: null}),
  "grade-delivery": () => ({title: "Grade delivery", subtitle: "Grading, gradebook and destination receipts.", action: null}),
  "follow-ups": () => ({title: "Student follow-up", subtitle: "Conversations and commitments that need you.", action: null}),
  "office-hours": () => ({title: "Office hours", subtitle: "Meetings and their follow-up.", action: null}),
  canvas: () => ({ title: "Canvas", subtitle: "Edit your Canvas modules, pages and assignments in Faculty and push them to every section.", action: null }),
  exams: () => ({ title: "Exams", subtitle: "Each assignment’s paper, seating, notices, collection and grading in one place.", action: add("New exam", "add-exam") }),
  // The plan page names its assessment and trail once it has read the plan.
  "exam-day": () => ({ title: "Exam-day packet", subtitle: "", crumb: "Exam-day packet", action: null }),
  grading: () => ({ title: "Grading", subtitle: "Read handwritten work and review rubric-grounded proposals.", action: null }),
  gradebook: () => ({ title: "Gradebook", subtitle: "Scores for each term, typed in, imported or pulled from Faculty’s grading; nothing is published or sent from here.", action: null }),
  "response-cards": () => ({ title: "Response Cards", subtitle: "Personal notices and forms, with each student’s response close at hand.", action: null }),
  accommodations: () => ({ title: "Accommodations", subtitle: "Who needs what, and who tests at the DR center.", action: null }),
  absences: () => ({ title: "Absences", subtitle: "Absences students ask you to excuse, their documentation and your decision.", action: null }),
  memory: () => ({ title: "Memory", subtitle: "What Faculty remembers, how it learns, and how much a request is told.", action: null }),
  recordings: () => ({ title: "Recordings", subtitle: "Recordings of your teaching, filed by the calendar.", action: null }),
  documents: () => ({ title: "Assignments", subtitle: "Create assignments and quizzes, and print their stamped copies.", action: null }),
  // Scans are what the Inbox mostly receives; New submission (rosters, documents, other files) sits in the page's summary card.
  // A scan's page keeps one primary, its own next step: uploading belongs to the list (review W1-DQ-2).
  inbox: ({ path }) => ({ title: "Teaching Inbox", subtitle: "Scans to review, work in progress, and completed results.", action: path.startsWith("inbox/job/") ? null : add("Upload scanned assignment", "inbox-upload-scans") }),
  seating: ({ detail, chart }) => detail
    ? {
        title: chart ? `${chart.title} · ${chart.room_name}` : "Seating chart",
        // The chart page sets its own context, trail and next step (its one primary) once it loads; nothing flashes before.
        subtitle: "",
        crumb: chart ? `${chart.title} · ${chart.room_name}` : "Seating chart",
        action: null,
      }
    : { title: "Seating charts", subtitle: "Place each section, check the room, and review the seats.", action: add("New seating chart", "add-seating") },
  rooms: ({ detail, room }) => detail
    ? {
        title: room ? room.name : "Room",
        subtitle: room?.notes || "Seat locations, room notes, and the conditions that affect seating.",
        crumb: room ? room.name : "Room",
        // The room page sets Room settings as its header action once it loads.
        action: null,
      }
    : { title: "Rooms", subtitle: "See the room before you seat the class.", action: add("Add a room", "add-room") },
  "all-courses": () => ({ title: "All courses", subtitle: "Every course, with its terms and sections in view.", action: add("Add a course", "add-course") }),
  "current-courses": () => ({ title: "Current courses", subtitle: "Rosters and materials for the courses you’re teaching now.", action: add("Add a course", "add-course") }),
  course: ({ group }) => group
    ? {
        title: group.code || "Course",
        subtitle: `${group.name} · ${courseTerm(group) || "Term not set"}`,
        crumb: `${group.code} · ${courseTerm(group)}`,
        // Settings are setup, never the page's main action (DESIGN-LANGUAGE §1.4; requirement, 2026-10-04): the course home has no primary; courses.js gives it Course settings as a secondary.
        action: null,
      }
    : { title: "Course", subtitle: "Course materials, term details, and section records.", crumb: "Course", action: null },
  tools: () => ({ title: "Your tools", subtitle: "The apps you use and the tools you want to build.", action: add("Add a tool", "add") }),
  pinned: () => ({ title: "Your everyday essentials.", subtitle: "Go straight to the tools you reach for most.", action: add("Add a tool", "add") }),
  roadmap: () => ({ title: "Good ideas start somewhere.", subtitle: "See what’s planned, taking shape, and ready to use.", action: add("Add a tool", "add") }),
};

/** The header defaults for the route on screen, from the views table. */
function headerDefaults() {
  const path = routePath(location.hash);
  const view = state.view;
  const context = {
    path,
    teacher: state.settings.teacher_name,
    group: view === "course" ? selectedCourseGroup() : null,
    detail: (view === "rooms" && Boolean(state.roomId)) || (view === "seating" && Boolean(state.seatingId)),
    room: view === "rooms" && state.roomId ? state.rooms.find((item) => item.id === state.roomId) : null,
    chart: view === "seating" && state.seatingId ? state.seatingCharts.find((item) => item.id === state.seatingId) : null,
  };
  const spec = (VIEWS[view] || VIEWS.overview)(context);
  const crumbs = [{ label: "Home", href: "#overview" }];
  if (view === "course") {
    crumbs.push({ label: SECTIONS[state.courseBackView][0], href: SECTIONS[state.courseBackView][1] }, { label: spec.crumb });
  } else if (view !== "overview") {
    const [label, href] = SECTIONS[view] || SECTIONS.overview;
    const detailLabel = spec.crumb || DETAIL_LABELS.find(([pattern]) => pattern.test(path))?.[1];
    crumbs.push(detailLabel && path !== view ? { label, href } : { label });
    if (detailLabel && path !== view) crumbs.push({ label: detailLabel });
  }
  return {
    route: path, variant: spec.variant || "compact", title: spec.title, subtitle: spec.subtitle, crumbs, action: spec.action,
  };
}

function render() {
  void assignmentsView.render(state.view);
  const courseDetail = state.view === "course";
  // Presentation only: a consistent accent follows each workspace area.
  document.body.dataset.workspaceView = courseDetail ? "courses" : state.view === "exam-day" ? "exams" : state.view;
  header.defaults(headerDefaults());
  header.setTitleParts({ name: state.settings.workspace_name });
  const currentCount = state.courseGroups.filter((course) => course.is_current).length;
  for (const [selector, value] of [
    ["#all-courses-count", state.courseGroups.length],
    ["#current-courses-count", currentCount],
    ['[data-nav-count="current-courses"]', currentCount],
    ["#rooms-count", state.counts.rooms],
    ["#seating-count", state.counts.seating],
  ]) {
    const node = $(selector);
    if (node) { node.textContent = value; node.toggleAttribute("data-zero", !value); }
  }
  $("#stat-total").textContent = state.tools.length;
  $("#stat-ready").textContent = state.tools.filter(
    (tool) => tool.status === "ready",
  ).length;
  $("#stat-building").textContent = state.tools.filter(
    (tool) => tool.status === "building",
  ).length;
  $("#overview-content").hidden = state.view !== "overview";
  $(".tools-section").hidden =
    !["tools", "pinned", "roadmap"].includes(state.view);
  $(".workspace-tip").hidden = !["tools", "pinned", "roadmap"].includes(state.view);
  $("#inbox-section").hidden = state.view !== "inbox";
  $("#documents-section").hidden = state.view !== "documents";
  $("#rooms-section").hidden = state.view !== "rooms";
  $("#seating-section").hidden = state.view !== "seating";
  $("#response-cards-section").hidden = state.view !== "response-cards";
  $("#exams-section").hidden = state.view !== "exams";
  $("#exam-day-section").hidden = state.view !== "exam-day";
  $("#grading-section").hidden = state.view !== "grading";
  $("#gradebook-section").hidden = state.view !== "gradebook";
  $("#calendar-section").hidden = state.view !== "calendar";
  $("#mail-section").hidden = state.view !== "mail";
  $("#canvas-section").hidden = state.view !== "canvas";
  $("#accommodations-section").hidden = state.view !== "accommodations";
  $("#absences-section").hidden = state.view !== "absences";
  $("#memory-section").hidden = state.view !== "memory";
  $("#recordings-section").hidden = state.view !== "recordings";
  $("#teaching-review-section").hidden = !["teaching-review", "grade-delivery"].includes(state.view);
  $("#student-support-section").hidden = !["follow-ups", "office-hours"].includes(state.view);
  $("#courses-section").hidden = !isCourseView();
  $("#course-detail").hidden = !courseDetail;
  highlightNav();
  document.querySelectorAll(".filter").forEach((node) => {
    const selected = node.dataset.status === state.filter;
    node.classList.toggle("active", selected);
    node.setAttribute("aria-pressed", selected);
  });
  $(".filter-tabs").hidden = state.view === "roadmap";
  $("#tools-description").textContent =
    state.view === "roadmap"
      ? "Edit a tool to move it from an idea to something you use."
      : state.view === "pinned"
        ? "The tools you’ve saved for a quicker start."
        : "A home for everything you build.";
  $("#tools-title").firstChild.textContent =
    state.view === "roadmap"
      ? "From idea to everyday "
      : state.view === "pinned"
        ? "Pinned tools "
        : "Your tools ";
  if (state.view === "overview") homeView.renderCourses(state.courseGroups);
  else if (state.view === "documents") documentsView.render();
  else if (state.view === "response-cards") responseCardsView.render();
  else if (state.view === "exams") examsView.render();
  else if (state.view === "exam-day") void examDayView.render();
  else if (state.view === "grading") gradingView.render();
  else if (state.view === "gradebook") gradebookView.render();
  else if (state.view === "calendar") calendarView.render();
  else if (state.view === "mail") mailView.render();
  else if (state.view === "canvas") void canvasHubView.render();
  else if (state.view === "accommodations") accommodationsView.render();
  else if (state.view === "absences") void absencesView.render();
  else if (state.view === "memory") void memoryView.render();
  else if (state.view === "recordings") void recordingsView.render();
  else if (["teaching-review", "grade-delivery"].includes(state.view)) void teachingReviewView.render();
  else if (["follow-ups", "office-hours"].includes(state.view)) void studentSupportView.render();
  else if (state.view === "rooms") roomsView.render();
  else if (state.view === "seating") seatingView.render();
  else if (state.view === "inbox") inboxView.render();
  else if (courseDetail) {
    renderCourseDetail();
    if (state.courseSub === "gradebook") gradebookView.render();
    if (state.courseSub === "canvas") void canvasContentView.render(state.courseId, false, state.courseSection);
    if (state.courseSub === "notes") { const group = selectedCourseGroup(); if (group) void courseNotesView.render(group, courseTerm(group)); }
    if (state.courseSub === "schedule") {
      const group = selectedCourseGroup();
      if (group && state.courseSection === null) void termScheduleView.render(group, courseTerm(group));
      else if (group) void classScheduleView.render(group, courseTerm(group), state.courseSection);
    }
    if (state.courseSub === "questions") { const group = selectedCourseGroup(); if (group) void questionBankView.render(group, courseTerm(group)); }
    // A course's rubric memories are on the Memory page now (ASSISTANT-MEMORY "ONE PAGE"): an old link lands there, on that course.
    if (state.courseSub === "rubric-memories") { const group = selectedCourseGroup(); if (group) location.replace(`#memory${group.definition_id ? `?course=${encodeURIComponent(group.definition_id)}` : ""}`); }
  }
  else if (isCourseView()) renderCourses();
  else if (state.loaded) renderTools();
}

function renderTools() {
  const query = state.query.toLocaleLowerCase().trim();
  const tools = state.tools
    .filter(
      (tool) =>
        (state.view !== "pinned" || tool.pinned) &&
        (state.view === "roadmap" ||
          state.filter === "all" ||
          tool.status === state.filter) &&
        (!query ||
          `${tool.name} ${tool.description} ${tool.category}`
            .toLocaleLowerCase()
            .includes(query)),
    )
    .sort((a, b) => {
      if (state.sort === "newest")
        return b.created_at.localeCompare(a.created_at);
      if (state.sort === "pinned" && a.pinned !== b.pinned)
        return Number(b.pinned) - Number(a.pinned);
      return a.name.localeCompare(b.name);
    });
  $("#tool-count").textContent = tools.length;
  if (state.view === "roadmap") {
    $("#tools-grid").innerHTML = `<div class="roadmap">${Object.entries(
      statusNames,
    )
      .map(([status, name]) => {
        const column = tools.filter((tool) => tool.status === status);
        return `<section class="roadmap-column"><h3><span class="status-dot ${status}"></span>${name}<span class="count-badge">${column.length}</span></h3>${column.map(toolCard).join("") || `<p class="roadmap-empty">${query ? "No matching tools." : { planned: "Room for your next idea.", building: "Your next project goes here.", ready: "Good things are on the way." }[status]}</p>`}</section>`;
      })
      .join("")}</div>`;
    return;
  }
  if (!tools.length) {
    const filtered = Boolean(query || state.filter !== "all");
    $("#tools-grid").innerHTML =
      `<div class="empty-state">${icon(filtered ? "search" : state.view === "pinned" ? "pin" : "sprout")}<h3>${filtered ? "No tools found." : state.view === "pinned" ? "Your favorites belong here." : "Make yourself at home."}</h3><p>${filtered ? "Try another search or show all of your tools." : state.view === "pinned" ? "Use the pin on any tool to keep it one click away." : "Add your first tool and start shaping your teaching workspace."}</p><button class="button" data-action="${filtered ? "clear-filters" : state.view === "pinned" ? "tools" : "add"}">${filtered ? "Clear filters" : state.view === "pinned" ? "Browse your tools" : "Add a tool"}</button></div>`;
  } else
    $("#tools-grid").innerHTML = tools.map(toolCard).join("");
}

function dialogHeader(title, subtitle = "") {
  return `<header class="dialog-header"><div><h2 id="dialog-title">${title}</h2>${subtitle ? `<p>${subtitle}</p>` : ""}</div><button type="button" class="icon-button" data-action="close" aria-label="Close dialog">${icon("close")}</button></header>`;
}

function showDialog(markup) {
  const dialog = $("#dialog");
  dialog.innerHTML = markup;
  // The page the dialog belongs to: leaving that page (Back, a link, the sidebar) closes it (see setView).
  if (!dialog.open) { dialog.dataset.route = location.hash.split("?")[0]; dialog.showModal(); }
}

/** A dialog opened by showDialog on another page (Back, a link) closes with the navigation; the leave guard has already asked about
 * unsaved input, and a dialog that is saving stays. */
function closeDialogOfLeftPage() {
  const dialog = $("#dialog");
  if (!dialog.open || !dialog.dataset.route || dialog.dataset.route === location.hash.split("?")[0]) return;
  if (dialog.querySelector('[data-saving="true"], [data-busy="true"]')) return;
  dialog.close();
}

function editTool(identifier) {
  const existing = state.tools.find((tool) => tool.id === identifier);
  if (identifier && !existing) {
    toast("This tool no longer exists.", true);
    return;
  }
  const tool = existing || {
    name: "",
    description: "",
    url: "",
    category: "Administration",
    status: "planned",
    icon: "grid",
    color: "sage",
    pinned: false,
  };
  dialogTool = { ...tool };
  showDialog(`<form id="tool-form">${dialogHeader(existing ? "A place in your workspace." : "Make room for a new tool.", existing ? "Make this tool work for you." : "Connect an app or save an idea for later.")}<div class="dialog-body">
    <label class="field">Tool name<input name="name" required maxlength="60" value="${escape(tool.name)}" placeholder="e.g. Course Rosters" autofocus></label>
    <label class="field">Description<span class="field-optional">optional</span><textarea name="description" maxlength="280" placeholder="What will this tool help you do?">${escape(tool.description)}</textarea></label>
    <div class="field-row"><label class="field">Category<select name="category" aria-label="Category">${categories.map((item) => `<option ${item === tool.category ? "selected" : ""}>${item}</option>`).join("")}</select></label><label class="field">Stage<select name="status" aria-label="Stage">${Object.entries(
      statusNames,
    )
      .map(
        ([value, label]) =>
          `<option value="${value}" ${value === tool.status ? "selected" : ""}>${label}</option>`,
      )
      .join("")}</select></label></div>
    <label class="field">Tool URL<span class="field-optional">required when ready</span><input name="url" maxlength="2048" value="${escape(tool.url)}" placeholder="https://your-tool.your-tailnet.ts.net" inputmode="url" spellcheck="false" autocapitalize="off" aria-describedby="url-hint"><span id="url-hint" class="field-hint">Use an address reachable from your other devices. A localhost URL opens on the device you’re using, not your home server. You can leave this blank while planning.</span></label>
    <fieldset class="choice-group"><legend>Icon</legend>${icons.map((item) => `<label class="icon-choice"><input type="radio" name="icon" value="${item}" ${item === tool.icon ? "checked" : ""} aria-label="${item} icon"><span>${icon(item)}</span></label>`).join("")}</fieldset>
    <fieldset class="choice-group"><legend>Color</legend>${colors.map((item) => `<label class="icon-choice color-choice"><input type="radio" name="color" value="${item}" ${item === tool.color ? "checked" : ""} aria-label="${item} color"><span class="${item}"></span></label>`).join("")}</fieldset>
    <label class="checkbox-field"><input type="checkbox" name="pinned" ${tool.pinned ? "checked" : ""}>Pin this tool for easy access</label><p id="form-error" class="form-error" role="alert" hidden></p>
    </div><footer class="dialog-footer">${existing ? `<button type="button" class="danger-button" data-action="delete-prompt">${icon("trash")}Remove</button>` : ""}<button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${existing ? "Save changes" : "Add tool"}</button></footer></form>`);
  $("#tool-form").addEventListener("submit", saveTool);
}

async function saveForm(form, operation, successMessage, afterSave) {
  if (form.dataset.saving === "true") return;
  const dialog = $("#dialog"), current = dialogSession(form, dialog), route = location.hash;
  const submit = form.querySelector('[type="submit"]');
  const label = submit?.textContent;
  if (submit) submit.textContent = "Saving…";
  try {
    await runFormAction(form, async () => {
      let result;
      try { result = await operation(); }
      catch (error) {
        if (current()) throw error;
        toast(error.message, true);
        return;
      }
      if (current()) dialog.close();
      if (await refresh(true) && location.hash === route) afterSave?.(result);
      toast(successMessage);
    });
  } finally {
    if (submit) submit.textContent = label;
  }
}

async function saveTool(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = Object.fromEntries(new FormData(form));
  data.pinned = form.elements.pinned.checked;
  if (dialogTool.id) data.version = dialogTool.version;
  await saveForm(
    form,
    () =>
      api(
        dialogTool.id ? `/api/tools/${dialogTool.id}` : "/api/tools",
        dialogTool.id ? "PATCH" : "POST",
        data,
      ),
    dialogTool.id ? "Tool updated." : "A new tool. A little more possibility.",
  );
}

function deletePrompt() {
  const tool = dialogTool;
  showDialog(
    `<form id="delete-form">${dialogHeader("Remove this tool?")}<div class="dialog-body"><p class="guide-intro">Remove <strong>${escape(tool.name)}</strong> from your workspace? This removes its saved shortcut and settings. The linked application stays where it is.</p><p id="form-error" class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="edit" data-id="${tool.id}">Keep tool</button><button class="button danger" type="submit">Remove tool</button></footer></form>`,
  );
  $("#delete-form").addEventListener("submit", (event) => {
    event.preventDefault();
    void saveForm(
      event.currentTarget,
      () => api(`/api/tools/${tool.id}`, "DELETE", { version: tool.version }),
      "Tool removed from your workspace.",
    );
  });
}

function editCourse(identifier) {
  const existing = state.courses.find((course) => course.id === identifier);
  if (identifier && !existing) {
    toast("This course no longer exists.", true);
    return;
  }
  if (existing && storagePending(existing)) {
    toast(
      "Course storage is being moved. Settings will be available when it finishes.",
      true,
    );
    return;
  }
  const course = {
    ...(existing || {
      name: "",
      code: "",
      term: "",
      term_other: "",
      year: "",
      crn: "",
      multiple_sections: false,
      sections: [],
      is_current: true,
    }),
  };
  const codeParts = (item) => {
    const match = item.code
      ?.trim()
      .match(/^([a-z]{1,20})[ \t-]*([0-9]{1,19})$/i);
    return {
      prefix: item.code_prefix || match?.[1]?.toUpperCase() || "",
      number: item.code_number || match?.[2] || "",
    };
  };
  const prefixes = [
    ...new Set(
      [
        "MATH",
        ...state.coursePrefixes,
        ...state.courses.map((item) => codeParts(item).prefix),
      ].filter(Boolean),
    ),
  ].sort();
  const parts = codeParts(course);
  // One choice per shared course code; use the most recently saved offering's name.
  const seenCodes = new Set();
  const previousCourses = [...state.courses]
    .sort(
      (a, b) =>
        b.updated_at.localeCompare(a.updated_at) || b.id.localeCompare(a.id),
    )
    .filter((item) => {
      if (seenCodes.has(item.code)) return false;
      seenCodes.add(item.code);
      return true;
    })
    .sort((a, b) => a.code.localeCompare(b.code, undefined, { numeric: true }));
  dialogCourse = course;
  showDialog(`<form id="course-form">${dialogHeader(existing ? "Course settings" : "Add a course", "Complete the course details below. CRN is optional.")}<div class="dialog-body">
    ${!existing ? `<div class="previous-course-panel"><label class="field">Previous course<select name="previous_course" aria-label="Previous course"><option value="new">New course — enter details</option>${previousCourses.map((item) => `<option value="${item.id}">${escape(item.code)} — ${escape(item.name)}</option>`).join("")}</select><span class="field-hint">Choose a previous course to fill only its name and code.</span></label></div>` : ""}
    <label class="field">Course name<input name="name" required maxlength="120" value="${escape(course.name)}" placeholder="e.g. Introduction to Biology" autofocus></label>
    <fieldset class="course-code-fields"><legend>Course code</legend>
    <div class="field-row"><div><label class="field">Course prefix<select name="code_prefix" required aria-controls="custom-prefix-field"><option value="" disabled ${!parts.prefix ? "selected" : ""}>Select a prefix</option>${prefixes.map((prefix) => `<option value="${escape(prefix)}" ${prefix === parts.prefix ? "selected" : ""}>${escape(prefix)}</option>`).join("")}<option value="__other__">Other</option></select></label>
    <label id="custom-prefix-field" class="field" hidden>Custom course prefix<input name="custom_prefix" maxlength="20" placeholder="e.g. PHYS" autocomplete="off" spellcheck="false" autocapitalize="characters" disabled></label></div>
    <label class="field">Course number<input name="code_number" aria-label="Course number" type="number" min="0" step="1" inputmode="numeric" required value="${escape(parts.number)}" placeholder="e.g. 101" aria-describedby="course-code-hint"><span class="field-hint">Numeric suffix</span></label></div>
    <p id="course-code-hint" class="field-hint"><strong id="course-code-preview"></strong><span>The same code links this course across terms and sections.${existing ? " Changing it links only this offering to a different course." : ""}</span></p>
    ${existing && !parts.prefix && course.code ? `<p class="field-hint">Previously saved code: ${escape(course.code)}. Enter its prefix and numeric suffix to update it.</p>` : ""}</fieldset>
    <label class="field">CRN<span class="field-optional">optional</span><input name="crn" maxlength="40" value="${escape(course.crn)}" placeholder="e.g. 01234"></label>
    <div class="field-row course-term-fields"><label class="field">Term<select name="term" aria-label="Term" required><option value="" disabled ${!course.term ? "selected" : ""}>Select a term</option>${terms.map((term) => `<option value="${term}" ${term === course.term ? "selected" : ""}>${term}</option>`).join("")}</select></label>
    <div class="field"><label class="field-label" for="course-year">Year</label><div class="year-picker"><input id="course-year" name="year" type="number" required min="1000" max="9999" step="1" inputmode="numeric" value="${escape(course.year)}" placeholder="${new Date().getFullYear()}" aria-describedby="year-hint"><div class="year-buttons"><button type="button" data-year-step="1" aria-label="Increase year">▴</button><button type="button" data-year-step="-1" aria-label="Decrease year">▾</button></div></div><span id="year-hint" class="field-hint">Scroll or use the arrows.</span></div></div>
    <label id="other-term-field" class="field" ${course.term !== "Other" ? "hidden" : ""}>Term identifier<input name="term_other" maxlength="60" value="${escape(course.term_other)}" placeholder="e.g. Winter session" ${course.term === "Other" ? "required" : "disabled"}></label>
    <div class="course-section-block"><label class="checkbox-field"><input type="checkbox" name="multiple_sections" ${course.multiple_sections ? "checked" : ""} aria-controls="course-sections">Multiple Sections?</label>
    <fieldset id="course-sections" class="course-sections"><legend id="section-legend">Section number</legend><div id="section-inputs" class="section-inputs"></div><button type="button" class="button" id="add-section">${icon("plus")}Add section</button><p id="section-hint" class="field-hint"></p></fieldset></div>
    <div class="course-coordinator-block"><label class="checkbox-field"><input type="checkbox" name="coordinated" aria-controls="course-coordinator" aria-describedby="course-coordinator-hint" ${existing ? "disabled" : ""}>Coordinated course</label>
    <fieldset id="course-coordinator" class="course-coordinator" disabled><legend>Coordinator</legend><div class="field-row"><label class="field">Name<input name="coordinator_name" maxlength="120" placeholder="e.g. Dr. Jordan Lee" autocomplete="off"></label><label class="field">Email<input name="coordinator_email" type="email" maxlength="254" placeholder="name@tamu.edu" autocomplete="off" spellcheck="false"></label></div></fieldset>
    <p id="course-coordinator-hint" class="field-hint">${existing ? "Reading this term’s coordinator…" : COORDINATOR_HINT}</p></div>
    <label class="checkbox-field"><input type="checkbox" name="is_current" ${course.is_current ? "checked" : ""} aria-describedby="current-course-hint">Current course</label><p id="current-course-hint" class="field-hint">Show this course in Current courses. Uncheck it when it’s no longer current; it will remain in All courses.${state.storage.enabled ? " Its data is copied to the HDD and verified before the NVMe copy is removed. Marking it current again restores it to NVMe." : ""}</p>
    <p id="form-error" class="form-error" role="alert" hidden></p></div>
    <footer class="dialog-footer">${existing ? `<button type="button" class="danger-button" data-action="delete-course-prompt">${icon("trash")}Remove</button>` : ""}<button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">${existing ? "Save changes" : "Add course"}</button></footer></form>`);
  const form = $("#course-form");
  const selectedPrefix = () =>
    (form.elements.code_prefix.value === "__other__"
      ? form.elements.custom_prefix.value
      : form.elements.code_prefix.value
    )
      .trim()
      .toUpperCase();
  function validateCode() {
    const number = form.elements.code_number.value;
    form.elements.code_number.setCustomValidity(
      number && !/^[0-9]{1,19}$/.test(number)
        ? "Enter 1–19 digits without decimals, signs, or exponents."
        : "",
    );
    const prefix = selectedPrefix();
    form.elements.custom_prefix.setCustomValidity(
      form.elements.code_prefix.value === "__other__" &&
        !/^[A-Z]{1,20}$/.test(prefix)
        ? "Use letters only for the course prefix."
        : "",
    );
    $("#course-code-preview").textContent =
      prefix && number ? `${prefix} ${number}` : "Prefix + number";
  }
  function updatePrefix() {
    const other = form.elements.code_prefix.value === "__other__";
    $("#custom-prefix-field").hidden = !other;
    form.elements.custom_prefix.disabled = !other;
    form.elements.custom_prefix.required = other;
    validateCode();
  }
  form.elements.code_prefix.addEventListener("change", () => {
    updatePrefix();
    if (!form.elements.custom_prefix.disabled)
      form.elements.custom_prefix.focus();
  });
  form.elements.custom_prefix.addEventListener("input", validateCode);
  form.elements.custom_prefix.addEventListener("blur", () => {
    form.elements.custom_prefix.value = form.elements.custom_prefix.value
      .trim()
      .toUpperCase();
    validateCode();
  });
  form.elements.code_number.addEventListener("input", validateCode);
  updatePrefix();
  const sectionValues = (course.sections || []).map(String);
  while (sectionValues.length < 2) sectionValues.push("");
  const sectionInputs = () => [...form.querySelectorAll('[name="section"]')];
  function validateSections() {
    const inputs = sectionInputs();
    inputs.forEach((input) => {
      const duplicate =
        input.value !== "" &&
        inputs.some(
          (other) =>
            other !== input &&
            other.value !== "" &&
            Number(other.value) === Number(input.value),
        );
      input.setCustomValidity(
        duplicate ? "Each section number must be different." : "",
      );
    });
  }
  function renderSections() {
    const multiple = form.elements.multiple_sections.checked;
    $("#add-section").hidden = !multiple;
    $("#section-legend").textContent = multiple
      ? "Section numbers"
      : "Section number";
    $("#section-hint").textContent = multiple
      ? "Enter a different number for each section. At least two are required."
      : "A section number is required for every course.";
    $("#section-inputs").classList.toggle("single-section", !multiple);
    $("#section-inputs").innerHTML = sectionValues
      .slice(0, multiple ? sectionValues.length : 1)
      .map(
        (value, index) =>
          `<div class="section-entry"><label class="field">Section ${index + 1}<input name="section" type="number" min="1" max="9007199254740991" step="1" required inputmode="numeric" value="${escape(value)}" placeholder="e.g. ${index + 1}"></label>${index >= 2 ? `<button type="button" class="icon-button remove-section" data-remove-section="${index}" aria-label="Remove section ${index + 1}">${icon("close")}</button>` : ""}</div>`,
      )
      .join("");
    validateSections();
  }
  function syncSectionValues() {
    // Keep additional values in the form draft when switching back to one section.
    sectionInputs().forEach((input, index) => {
      sectionValues[index] = input.value;
    });
  }
  renderSections();
  form.elements.previous_course?.addEventListener("change", (event) => {
    const previous = previousCourses.find(
      (item) => item.id === event.target.value,
    );
    if (previous) {
      form.elements.name.value = previous.name;
      const parts = codeParts(previous);
      form.elements.code_prefix.value = parts.prefix;
      form.elements.code_number.value = parts.number;
      form.elements.custom_prefix.value = "";
      updatePrefix();
    }
  });
  form.elements.term.addEventListener("change", () => {
    const other = form.elements.term.value === "Other";
    $("#other-term-field").hidden = !other;
    form.elements.term_other.disabled = !other;
    form.elements.term_other.required = other;
    if (other) form.elements.term_other.focus();
  });
  form.elements.multiple_sections.addEventListener("change", () => {
    syncSectionValues();
    renderSections();
  });
  // COORDINATOR: the name and email stay greyed out until Coordinated course is ticked. The term owns the answer, so an
  // existing course reads it when its card opens; until that read comes back the card leaves it alone on Save.
  let coordinatorRead = !existing;
  function updateCoordinator() {
    const on = form.elements.coordinated.checked;
    $("#course-coordinator").disabled = !on;
    form.elements.coordinator_name.required = on;
    form.elements.coordinator_email.required = on;
  }
  form.elements.coordinated.addEventListener("change", () => {
    updateCoordinator();
    if (form.elements.coordinated.checked) form.elements.coordinator_name.focus();
  });
  updateCoordinator();
  if (existing) {
    void api(`/api/courses/${course.id}/coordinator`).then(
      (saved) => {
        if (dialogCourse !== course || !form.isConnected) return;
        form.elements.coordinated.checked = saved.coordinated;
        form.elements.coordinator_name.value = saved.name;
        form.elements.coordinator_email.value = saved.email;
        form.elements.coordinated.disabled = false;
        coordinatorRead = true;
        $("#course-coordinator-hint").textContent = COORDINATOR_HINT;
        updateCoordinator();
      },
      () => {
        if (form.isConnected)
          $("#course-coordinator-hint").textContent =
            "This term’s coordinator could not be read. Saving leaves it as it is; close and reopen this card to change it.";
      },
    );
  }
  $("#section-inputs").addEventListener("input", validateSections);
  $("#section-inputs").addEventListener("click", (event) => {
    const remove = event.target.closest("[data-remove-section]");
    if (!remove) return;
    syncSectionValues();
    sectionValues.splice(Number(remove.dataset.removeSection), 1);
    renderSections();
    $("#add-section").focus();
  });
  $("#add-section").addEventListener("click", () => {
    syncSectionValues();
    sectionValues.push("");
    renderSections();
    sectionInputs().at(-1).focus();
  });
  const year = form.elements.year;
  function stepYear(step) {
    const value = Number(year.value);
    year.value = String(
      Math.min(
        9999,
        Math.max(
          1000,
          year.value && Number.isInteger(value)
            ? value + step
            : new Date().getFullYear(),
        ),
      ),
    );
    year.dispatchEvent(new Event("input", { bubbles: true }));
  }
  form.querySelectorAll("[data-year-step]").forEach((button) => {
    button.addEventListener("click", () =>
      stepYear(Number(button.dataset.yearStep)),
    );
  });
  year.addEventListener(
    "wheel",
    (event) => {
      if (document.activeElement !== year || event.deltaY === 0) return;
      event.preventDefault();
      stepYear(event.deltaY < 0 ? 1 : -1);
    },
    { passive: false },
  );
  year.addEventListener("keydown", (event) => {
    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      event.preventDefault();
      stepYear(event.key === "ArrowUp" ? 1 : -1);
    }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const from = location.hash;
    const data = {
      name: form.elements.name.value,
      code: `${selectedPrefix()} ${form.elements.code_number.value}`,
      code_prefix: selectedPrefix(),
      code_number: form.elements.code_number.value,
      crn: form.elements.crn.value,
      term: form.elements.term.value,
      term_other:
        form.elements.term.value === "Other"
          ? form.elements.term_other.value
          : "",
      year: Number(form.elements.year.value),
      multiple_sections: form.elements.multiple_sections.checked,
      sections: sectionInputs().map((input) => Number(input.value)),
      is_current: form.elements.is_current.checked,
    };
    if (coordinatorRead) {
      const coordinated = form.elements.coordinated.checked;
      data.coordinator = {
        coordinated,
        name: coordinated ? form.elements.coordinator_name.value : "",
        email: coordinated ? form.elements.coordinator_email.value : "",
      };
    }
    if (course.id) data.version = course.version;
    void saveForm(
      form,
      async () => {
        const saved = await api(
          course.id ? `/api/courses/${course.id}` : "/api/courses",
          course.id ? "PATCH" : "POST",
          data,
        );
        if (!course.id && location.hash === from) {
          state.query = "";
          $("#course-search").value = "";
          // A non-current course should remain visible after adding it from Current courses.
          if (state.view === "current-courses" && !saved.is_current)
            location.hash = "all-courses";
        }
        return saved;
      },
      course.id ? "Course updated." : "Course added to your workspace.",
      (saved) => {
        if (state.view !== "course") return;
        const group = state.courseGroups.find((item) =>
          item.offerings.some((offering) => offering.id === saved.id),
        );
        if (!group) return;
        const section = group.sections.includes(state.courseSection)
          ? state.courseSection
          : null;
        const destination = courseHref(group, section);
        if (location.hash !== destination) location.hash = destination;
        else
          document
            .querySelector('#course-detail [role="tab"][aria-selected="true"]')
            ?.focus({ preventScroll: true });
      },
    );
  });
}

function deleteCoursePrompt() {
  const course = dialogCourse;
  showDialog(
    `<form id="delete-course-form">${dialogHeader("Remove this course?")}<div class="dialog-body"><p>Remove <strong>${escape(course.name)}</strong> and its saved details from your workspace? To keep it for another term, uncheck Current course instead.${state.storage.enabled ? " Removing it retains a final verified HDD backup and clears its NVMe copy." : ""}</p><p id="form-error" class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-action="edit-course" data-id="${course.id}">Keep course</button><button type="submit" class="button danger">Remove course</button></footer></form>`,
  );
  $("#delete-course-form").addEventListener("submit", (event) => {
    event.preventDefault();
    void saveForm(
      event.currentTarget,
      () =>
        api(`/api/courses/${course.id}`, "DELETE", { version: course.version }),
      "Course removed.",
      () => {
        if (state.view !== "course") return;
        const group = selectedCourseGroup();
        if (!group) location.hash = state.courseBackView;
        else if (
          state.courseSection !== null &&
          !group.sections.includes(state.courseSection)
        )
          location.hash = courseHref(group);
        else
          document
            .querySelector('#course-detail [role="tab"][aria-selected="true"]')
            ?.focus({ preventScroll: true });
      },
    );
  });
}

/* Workspace settings is the settings hub: the workspace's own settings, links to the settings each page keeps, storage,
   the access guide (formerly the sidebar's Connection guide) and the export. */
const ELSEWHERE = [
  // Each link opens its control on that page (the page reads the query, opens the control, then drops the query).
  ["#memory", "Memory", "What the Assistant and the rubric drafter remember, how Faculty learns, and how much a request is told (opens Memory)."],
  ["#recordings", "Recordings", "The recordings of your teaching that Faculty keeps, and the ones waiting for you to keep or not (opens Recordings)."],
  ["#grading?settings=1", "Grading models and compute", "Cloud connections, the assistant’s model, reasoning and costs (opens Compute settings)."],
  ["#response-cards?email", "Email delivery", "Gmail and the default sender (opens Email settings)."],
  ["#inbox?codes", "Codes & API access", "Upload codes and API keys (opens Codes & API access)."],
  ["#mail", "Mail (Gmail)", "Read your mail, see a student’s mail and write from Faculty; opens Mail, where Gmail is connected."],
  ["#calendar?google=1", "Google Calendar sync", "Faculty's events in your Google calendar, and your Google events here (opens Google Calendar)."],
  ["#documents?tab=batches", "Print email recipients", "Chosen per print job under Export by email, from the approved addresses."],
];

function guideMarkup() {
  return `<div class="guide-body"><h3 class="settings-guide-title">Your workspace, anywhere.</h3><p class="guide-intro">Faculty runs on your home server. Tailscale gives your devices a private way to reach it.</p><section class="guide-step"><span class="step-number">1</span><h3>Start your workspace</h3><p>On the server, from the project folder, run:</p><code class="code-block">systemctl --user start faculty</code><p>The workspace listens at <code>127.0.0.1:3000</code> on the server. You can also run it directly with Python; see the project README.</p></section><section class="guide-step"><span class="step-number">2</span><h3>Connect it to your tailnet</h3><p>With Tailscale installed and signed in on the server, run:</p><code class="code-block">tailscale serve --bg http://127.0.0.1:3000</code><p>Follow any Tailscale setup prompt. It will print an HTTPS address for this workspace.</p></section><section class="guide-step"><span class="step-number">3</span><h3>Take your workspace with you</h3><p>Sign in to Tailscale on your laptop or phone, then open that HTTPS address. Bookmark it and you’re home.</p></section><div class="guide-note"><strong>Prefer an SSH tunnel?</strong><br>Forward the workspace port from your laptop:<code class="code-block">ssh -N -L 3000:127.0.0.1:3000 user@your-server</code>Then open <code>http://localhost:3000</code> on that laptop.</div><div class="guide-note">This is a personal workspace with no application login. Limit access to your trusted devices using your Tailscale access rules. Each linked tool needs its own reachable address or proxy route. Faculty doesn’t configure Tailscale or proxy linked apps.</div><a class="guide-link" href="https://tailscale.com/docs/reference/tailscale-cli/serve" target="_blank" rel="noopener noreferrer">Tailscale Serve documentation ${icon("arrow-up-right")}</a></div>`;
}

function showSettings({ section = "" } = {}) {
  const settings = { ...state.settings };
  showDialog(
    `<form id="settings-form" class="settings-hub">${dialogHeader("Workspace settings", "Your name, storage and where each page keeps its own settings.")}<div class="dialog-body">
    <section class="settings-section" aria-labelledby="settings-profile-title"><h3 id="settings-profile-title">Profile</h3>
      <label class="field">Workspace name<input name="workspace_name" value="${escape(settings.workspace_name)}" maxlength="60" required autofocus aria-describedby="settings-name-hint"></label><p id="settings-name-hint" class="field-hint settings-hint">Shown in the browser tab after each page’s name.</p><label class="field">Your name<span class="field-optional">optional</span><input name="teacher_name" value="${escape(settings.teacher_name)}" placeholder="What should we call you?" maxlength="40"></label>
    </section>
    <section class="settings-section" aria-labelledby="settings-elsewhere-title"><h3 id="settings-elsewhere-title">Settings elsewhere</h3>
      <ul class="settings-links">${ELSEWHERE.map(([href, label, detail]) => `<li><a class="settings-link" href="${href}" data-settings-link>${escape(label)}${icon("arrow-right")}</a><span>${escape(detail)}</span></li>`).join("")}</ul>
    </section>
    <section class="settings-section" aria-labelledby="settings-storage-title"><h3 id="settings-storage-title">Storage &amp; backups</h3><div id="settings-storage"><p class="loading-state">Checking storage…</p></div></section>
    <section class="settings-section" aria-labelledby="settings-access-title"><h3 id="settings-access-title">Access from other devices</h3>${disclosureMarkup({ label: "Connection guide", body: guideMarkup(), open: section === "access", id: "settings-access", className: "settings-access" })}</section>
    <div class="export-section settings-section"><div><h3>Keep a copy of your workspace</h3><p>Export your courses, tools, and settings as a JSON file. Linked apps and their data are separate.</p></div><a class="button" href="/api/export" download>${icon("download")}Export</a></div><p id="form-error" class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button class="button" type="button" data-action="close">Cancel</button><button class="button primary" type="submit">Save changes</button></footer></form>`,
  );
  const form = $("#settings-form");
  // A link to another page's settings closes the dialog on the way.
  form.querySelectorAll("[data-settings-link]").forEach((link) => link.addEventListener("click", () => $("#dialog").close()));
  if (section === "access") {
    const access = $("#settings-access");
    requestAnimationFrame(() => access?.scrollIntoView({ block: "start" }));
    access?.querySelector("summary")?.focus({ preventScroll: true });
  }
  const storageHost = $("#settings-storage");
  void api("/api/state?include=storage").then(data => {
    if (!storageHost.isConnected) return;
    state.storage = data.storage;
    storageHost.innerHTML = storageSummary();
  }).catch(error => {
    if (storageHost.isConnected) storageHost.textContent = `Storage status unavailable. ${error.message}`;
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = {
      ...Object.fromEntries(new FormData(form)),
      version: settings.version,
    };
    void saveForm(
      form,
      () => api("/api/settings", "PATCH", data),
      "Workspace settings saved.",
    );
  });
}

// The Connection guide lives in Workspace settings; the old action opens it there.
function showGuide() {
  showSettings({ section: "access" });
}

/* Navigation (UX overhaul): a new page (the route's path changed) starts at its top with focus on its title; Back and
   Forward return to where that page was scrolled. Each history entry carries a key in history.state, so a traversal can be
   told from a new navigation (a fragment navigation also fires popstate). Positions live in sessionStorage per entry. */
const SCROLL_PREFIX = "faculty:scroll:";
let entryKey = "";
let previousPath = null;
let restoreTarget = null;

function rememberScroll() {
  if (!entryKey) return;
  try { sessionStorage.setItem(SCROLL_PREFIX + entryKey, String(Math.round(window.scrollY))); } catch { /* a convenience only */ }
}

function historyEntry() {
  const current = history.state && typeof history.state === "object" ? history.state : null;
  if (current?.facultyEntry) return { key: current.facultyEntry, traversal: true };
  const key = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  try { history.replaceState({ ...(current || {}), facultyEntry: key }, "", location.href); } catch { /* keep going without restore */ }
  return { key, traversal: false };
}

// Views rewrite their own address with history.replaceState(history.state, …) (calendar, grading, gradebook and documents links);
// an older call may still pass null.
// That would drop the entry's key, and Back to such a page would start at its top: keep the key on the rewritten entry
// (review W1-FP-1). Only this page's code calls replaceState, and none of it reads history.state.
{
  const replace = history.replaceState.bind(history);
  history.replaceState = (data, unused, url) => {
    const key = history.state && typeof history.state === "object" ? history.state.facultyEntry : "";
    const keep = key && (data === null || data === undefined || (typeof data === "object" && !Array.isArray(data) && !data.facultyEntry));
    return replace(keep ? { ...(data || {}), facultyEntry: key } : data, unused, url);
  };
}

function savedScroll(key) {
  try {
    const value = sessionStorage.getItem(SCROLL_PREFIX + key);
    return value === null ? null : Number(value);
  } catch {
    return null;
  }
}

// Content arrives asynchronously: try again as it renders, until the instructor scrolls or the position is reached.
function restoreScroll() {
  if (restoreTarget === null) return;
  const target = restoreTarget;
  const reachable = document.documentElement.scrollHeight - window.innerHeight >= target - 2;
  window.scrollTo({ top: target, behavior: "instant" });
  if (reachable) restoreTarget = null;
}
["wheel", "touchstart", "keydown", "pointerdown"].forEach((type) => window.addEventListener(type, () => { restoreTarget = null; }, { passive: true, capture: true }));
window.addEventListener("scroll", () => {
  clearTimeout(rememberScroll.timer);
  rememberScroll.timer = setTimeout(rememberScroll, 120);
}, { passive: true });

// Recent places for the palette: recorded once the page has named its object (views set their header after their reads).
let placeTimer = 0;
function schedulePlace() {
  clearTimeout(placeTimer);
  const hash = location.hash;
  placeTimer = setTimeout(() => {
    if (location.hash !== hash) return;
    const spec = header.get();
    const trail = (spec.crumbs || []).slice(1).map((crumb) => crumb.label).filter(Boolean);
    // A scan, a grading plan and a gradebook are one place each (`placeKey`): the object is the label (the scan's headline, the
    // plan's assignment) and the page within it the detail. Elsewhere the last crumb names the place. Each part is named once.
    const path = hash.replace(/^#/, "").split("?")[0];
    const object = /^(inbox\/job|grading|gradebook)\//.test(path);
    const label = (object ? spec.title || trail.at(-1) : trail.at(-1) || spec.title) || "";
    const within = object ? (trail.length && trail.at(-1) !== label ? [trail.at(-1)] : []) : trail.slice(0, -1);
    const seen = new Set([label]);
    const detail = [...within, spec.context].flatMap((part) => String(part || "").split(" · "))
      .map((part) => part.trim()).filter((part) => part && !seen.has(part) && seen.add(part));
    rememberPlace({ hash, label, detail: detail.join(" · ") });
  }, 1500);
}

function setView() {
  closeDialogOfLeftPage();
  if (/^#documents\?(?:create=1|quiz=new)(?:&|$)/.test(location.hash)) {
    history.replaceState(history.state, '', location.hash.replace('#documents?', '#assignments?'));
  }
  if (["#documents", "#exams", "#grading"].includes(location.hash)) {
    const from = location.hash;
    history.replaceState(history.state, "", `#assignments${from === "#grading" ? "?filter=grading" : ""}`);
  }
  const [target, query = ""] = location.hash.slice(1).split("?");
  const wasTabFocused = document.activeElement?.getAttribute("role") === "tab";
  const wasSectionShortcutFocused =
    document.activeElement?.classList.contains("section-shortcut");
  const previousView = state.view;
  const previousCourseId = state.courseId;
  const previousSection = state.courseSection;
  // The page being left keeps its position. On Back/Forward the browser may already have moved the scroll, so only the
  // positions saved while scrolling count there.
  clearTimeout(rememberScroll.timer);
  if (!history.state?.facultyEntry) rememberScroll();
  const entry = historyEntry();
  entryKey = entry.key;
  if (target === "assignments" || target.startsWith("assignments/")) {
    state.view = "assignments";
  } else if (target === "documents" || target.startsWith("documents/")) {
    state.view = "documents";
  } else if (target === "exams" || target.startsWith("exams/")) {
    state.view = "exams";
  } else if (target.startsWith("exam-day/")) {
    state.view = "exam-day";
  } else if (target === "grading" || target.startsWith("grading/")) {
    state.view = "grading";
  } else if (target === "gradebook" || target.startsWith("gradebook/")) {
    state.view = "gradebook";
  } else if (target === "response-cards" || target.startsWith("response-cards/")) {
    state.view = "response-cards";
  } else if (target === "seating" || target.startsWith("seating/")) {
    state.view = "seating";
    state.seatingId = target.startsWith("seating/") ? target.slice(8) : "";
    try {
      state.seatingId = decodeURIComponent(state.seatingId);
    } catch {
      /* keep the raw identifier; the seating view reports it as unavailable */
    }
  } else if (target === "rooms" || target.startsWith("rooms/")) {
    state.view = "rooms";
    state.roomId = target.startsWith("rooms/") ? target.slice(6) : "";
    try {
      state.roomId = decodeURIComponent(state.roomId);
    } catch {
      /* keep the raw identifier; the room view reports it as unavailable */
    }
  } else if (target === "inbox" || target.startsWith("inbox/")) {
    state.view = "inbox";
  } else if (target === "mail" || target.startsWith("mail/")) {
    state.view = "mail";
  } else if (target === "canvas") {
    state.view = "canvas";
  } else if (target === "absences") {
    state.view = "absences";
  } else if (target === "memory") {
    state.view = "memory";
  } else if (target === "recordings" || target.startsWith("recordings/")) {
    state.view = "recordings";
  } else if (/^(teaching-review|grade-delivery)(\/|$)/.test(target)) {
    state.view = target.split("/")[0];
  } else if (["follow-ups", "office-hours"].includes(target)) {
    state.view = target;
  } else if (target.startsWith("course/")) {
    // A section tab IS its gradebook, where the roster was (requirement, 2026-09-28); its roster is .../roster and the gradebook's
    // setup .../setup, its changes .../changes. The course Home is its overview; .../home/gradebook lists the sections to choose from.
    // The course Home's .../home/canvas is the term's Canvas modules and pages (canvas-content.js); a section's .../canvas is its
    // own Canvas course edited directly (the same editor in section mode). The course Home's .../home/notes is the course's lecture
    // notes (course-notes.js), one set for every term and section, .../home/questions its question bank (question-bank.js) and
    // .../home/rubric-memories its rubric memories (rubric-memories.js).
    // The course Home's .../home/schedule is the term's weekly Term schedule; a section's .../schedule is its Class schedule.
    const match = target.match(/^course\/([^/]+)\/(home|section\/([0-9]+))(\/gradebook|\/canvas|\/notes|\/questions|\/rubric-memories|\/schedule)?(\/setup|\/changes|\/roster)?$/);
    state.view = "course";
    state.courseId = match?.[1] || "";
    state.courseSection = match?.[3] ? Number(match[3]) : null;
    state.courseSub = !match ? "overview" : match[3] ? (match[4] === "/canvas" ? "canvas" : match[4] === "/schedule" ? "schedule" : match[5] === "/roster" ? "roster" : "gradebook")
      : match[4] === "/canvas" ? "canvas" : match[4] === "/notes" ? "notes" : match[4] === "/questions" ? "questions"
        : match[4] === "/rubric-memories" ? "rubric-memories" : match[4] === "/schedule" ? "schedule" : match[4] ? "gradebook" : "overview";
    // The gradebook's setup and changes pages stand alone, without the section's work list.
    state.courseSetup = match?.[4] !== "/schedule" && (match?.[5] === "/setup" || match?.[5] === "/changes");
    state.courseBackView =
      new URLSearchParams(query).get("from") === "current-courses"
        ? "current-courses"
        : "all-courses";
  } else
    state.view = [
      "overview",
      "calendar",
      "tools",
      "all-courses",
      "current-courses",
      "accommodations",
      "pinned",
      "roadmap",
    ].includes(target)
      ? target
      : "overview";
  state.filter = "all";
  state.query = "";
  $("#search").value = "";
  const courseSearch = $("#course-search");
  if (courseSearch) courseSearch.value = "";
  const path = routePath(location.hash);
  const pathChanged = previousPath !== null && path !== previousPath;
  previousPath = path;
  if (pathChanged) dismissPageToasts(path);
  // Moving between the tabs of one course keeps focus on its tab strip (below), and #overview?operations opens Home's
  // health panel where it is.
  const sameCourse = state.view === "course" && previousView === "course" && previousCourseId === state.courseId;
  const operations = state.view === "overview" && query === "operations";
  const newPage = pathChanged && !sameCourse && !operations;
  restoreTarget = null;
  if (newPage) {
    // Focus goes to the new page's title, unless the instructor is already typing in a field of it.
    const active = document.activeElement;
    const typing = active instanceof HTMLElement && (active.isContentEditable || active.matches("input:not([type=button], [type=submit], [type=checkbox], [type=radio]), textarea, select"));
    if (!typing) $("#page-title").focus({ preventScroll: true });
    const saved = entry.traversal ? savedScroll(entry.key) : null;
    window.scrollTo({ top: 0, behavior: "instant" });
    if (saved) restoreTarget = saved;
  }
  render();
  restoreScroll();
  void refresh(true).then(() => requestAnimationFrame(restoreScroll));
  if (pathChanged || !placeTimer) schedulePlace();
  if (restoreTarget !== null) [250, 700, 1500].forEach((delay) => setTimeout(restoreScroll, delay));
  if (
    sameCourse &&
    (wasTabFocused ||
      wasSectionShortcutFocused ||
      previousSection !== state.courseSection)
  ) {
    const tab = document.querySelector(
      '#course-detail [role="tab"][aria-selected="true"]',
    );
    (tab || $("#course-panel"))?.focus({ preventScroll: true });
    tab?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  // The rail always shows the page's entry (rest-shell-3, 2026-10-02).
  if (!operations) revealNavEntry();
}

// Whether the assignment shell's context is on screen (ASSIGNMENT-WORKSPACE; the shell says so with `faculty:assignment-context`):
// a scan page opened inside an assignment is that assignment's Collect, so the rail highlights Assignments, not the Teaching Inbox.
// (`assignmentContext` is declared with the shell's other state, above, so a render before this line can read it.)
window.addEventListener("faculty:assignment-context", (event) => {
  assignmentContext = Boolean(event.detail?.active);
  highlightNav();
});

// DR REQUEST EMAILS (SCHEDULING, 2026-11-01): an approved Follow-up card's upload to AIM (assistant.js followAimUpload) started AIM's
// sign-in on the server; its window opens here, wherever the card was approved, so the instructor reads Duo's code and sees the end.
window.addEventListener("faculty:aim-upload", (event) => {
  const upload = /** @type {CustomEvent} */ (event).detail;
  const quiz = upload?.aim_upload?.title || "the quiz";
  const ended = (/** @type {any} */ status) => {
    document.dispatchEvent(new CustomEvent("accommodations-imported"));
    window.dispatchEvent(new CustomEvent("faculty:assistant-actions-changed"));
    const lines = status?.sent || [];
    const trouble = lines.some((/** @type {any} */ line) => line.did === "stopped" || /_(failed|uncertain|held)$/.test(line.did));
    const uploaded = lines.find((/** @type {any} */ line) => line.did === "uploaded");
    toast(trouble ? `The upload of ${quiz} needs a look: see Accommodations › DR quizzes.`
      : uploaded ? `Uploaded the accessible copy of ${quiz} to AIM (${uploaded.detail}).` : `Nothing new to upload for ${quiz}: AIM's requests already have it.`, trouble);
  };
  void openAimUpload({ api, showDialog, dialogHeader, escape, attach: upload, onDone: ended,
    onExported: (/** @type {any} */ result) => { ended(result); toast("Open Accommodations to import the DR bookings.", true); } });
});

// The rail's highlight (page-header.js `navEntry`): the shell's context, else the shell's own mark (removed without a context).
function highlightNav() {
  const inAssignment = assignmentContext || Boolean(document.querySelector("#assignment-workspace[data-area]"));
  const navView = navEntry({ view: state.view, courseSub: state.courseSub, path: routePath(location.hash), inAssignment });
  document.querySelectorAll(".sidebar [data-view]").forEach((node) => {
    const selected = node.dataset.view === navView;
    node.classList.toggle("active", selected);
    if (selected) node.setAttribute("aria-current", "page");
    else node.removeAttribute("aria-current");
  });
}

// A window made shorter keeps the page's entry above the rail's footer too (once per frame).
let railReveal = 0;
window.addEventListener("resize", () => {
  if (railReveal) return;
  railReveal = requestAnimationFrame(() => { railReveal = 0; revealRailEntry(); });
});
// The page's own entry, as render() marked it (one Courses entry for both lists and every course page, the Canvas entry for a
// course's Canvas pages, Exams for exam day), so the rail reveals what is highlighted.
const activeNavEntry = () => document.querySelector(".sidebar nav [data-view].active");
// On a short window the rail scrolls under its sticky footer: keep the current page's entry above the footer (again once the
// fonts have loaded, since they change the rows' heights). Only the rail moves, never the page.
function revealNavEntry() {
  revealRailEntry();
  document.fonts?.ready.then(() => requestAnimationFrame(() => revealRailEntry()));
}

/** Scroll the desktop rail (never the page) so the page's entry is between the rail's top and its sticky footer. */
function revealRailEntry() {
  const rail = document.querySelector("body > .sidebar");
  const entry = activeNavEntry();
  const foot = rail?.querySelector(".sidebar-bottom");
  if (!(rail instanceof HTMLElement) || !entry || !foot || rail.scrollHeight <= rail.clientHeight) return;
  const box = entry.getBoundingClientRect(), footTop = foot.getBoundingClientRect().top, top = rail.getBoundingClientRect().top;
  if (box.bottom > footTop) rail.scrollTop += box.bottom - footTop + 8;
  else if (box.top < top) rail.scrollTop -= top - box.top;
}

document.addEventListener("click", async (event) => {
  const filter = event.target.closest(".stat[data-status], .filter[data-status]");
  if (filter) {
    state.filter = filter.dataset.status;
    if (state.loaded) render();
    if (filter.classList.contains("stat"))
      $(".tools-section").scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    return;
  }
  const button = event.target.closest("[data-action]");
  if (!button || button.disabled) return;
  const action = button.dataset.action;
  if (["add", "add-course", "settings"].includes(action) && !state.loaded) {
    toast("Connect to your server first.", true);
    return;
  }
  if (action === "inbox-upload-scans") inboxView.uploadScans();
  if (action === "add-exam") void examsView.openCreate();
  if (action === "add-calendar-event") void calendarView.openCreate();
  if (action === "mail-compose") mailView.compose();
  if (action === "add-seating") void seatingView.openCreate(button.dataset.assignment || "");
  if (action === "add-room") roomsView.openAdd();
  if (action === "import-roster") importRosterDialog();
  if (["import-accommodations", "import-dr-exams"].includes(action)) void importAccommodations({api, showDialog, dialogHeader, escape, toast, kind: action === "import-dr-exams" ? "exams" : "accommodations", onImported: () => { void loadRosterPanel(); if (state.view === "accommodations") void accommodationsView.render(true); }});
  if (action === "record-absence") void absencesView.openNew({});
  if (action === "student-note") void studentNotesView.openNew();
  if (action === "retry-roster") void loadRosterPanel();
  if (action === "add") editTool();
  if (action === "add-course") editCourse();
  if (action === "edit-course") editCourse(button.dataset.id);
  if (action === "group-settings") {
    const group = state.courseGroups.find(
      (item) => item.id === button.dataset.groupId,
    );
    if (group) {
      const destination = courseHref(group);
      if (location.hash !== destination) {
        window.addEventListener(
          "hashchange",
          () => {
            $("#course-settings")?.focus();
          },
          { once: true },
        );
        location.hash = destination;
      } else $("#course-settings")?.focus();
    }
  }
  if (action === "delete-course-prompt") deleteCoursePrompt();
  if (action === "all-courses") location.hash = "all-courses";
  if (action === "clear-course-search") {
    state.query = "";
    $("#course-search").value = "";
    renderCourses();
  }
  if (action === "edit") editTool(button.dataset.id);
  if (action === "close") $("#dialog").close();
  if (action === "delete-prompt") deletePrompt();
  if (action === "settings") showSettings();
  if (action === "palette") palette.open();
  if (action === "guide") showGuide();
  if (action === "tools") location.hash = "tools";
  if (action === "refresh") {
    setBusy(button, true);
    const success = await refresh();
    setBusy(button, false);
    if (success) toast("Workspace refreshed.");
  }
  if (action === "clear-filters") {
    state.query = "";
    state.filter = "all";
    $("#search").value = "";
    render();
  }
  if (action === "pin") {
    const tool = state.tools.find((item) => item.id === button.dataset.id);
    if (!tool) return;
    setBusy(button, true);
    try {
      await api(`/api/tools/${tool.id}`, "PATCH", {
        pinned: !tool.pinned,
        version: tool.version,
      });
      await refresh(true);
      document
        .querySelector(`[data-action="pin"][data-id="${tool.id}"]`)
        ?.focus({ preventScroll: true });
      toast(tool.pinned ? "Tool unpinned." : "Pinned for easy access.");
    } catch (error) {
      toast(error.message, true);
    } finally {
      setBusy(button, false);
    }
  }
});

$("#search").addEventListener("input", (event) => {
  state.query = event.target.value;
  if (state.loaded) renderTools();
});
$("#sort").addEventListener("change", (event) => {
  state.sort = event.target.value;
  if (state.loaded) renderTools();
});
/* The command palette (Ctrl K / ⌘K) and its shortcut sheet ('?'). Actions open the same dialogs as their buttons: the
   palette goes to the owning page first, then presses the page's own control. */
const palette = createPalette({
  api,
  icon,
  openStudent: (target) => gradebookView.openStudentRecord(target),
  pages: () => [
    ...[...document.querySelectorAll(".sidebar nav a.nav-item")].map((link) => ({ group: "Pages", label: link.getAttribute("aria-label") || link.textContent.trim(), href: link.getAttribute("href") })),
    { group: "Pages", label: "Workspace settings", detail: "Profile, storage and settings elsewhere", run: () => showSettings() },
  ],
  actions: () => PALETTE_ACTIONS.map(([label, route, action, detail]) => ({ group: "Actions", label, detail, run: () => void perform(route, action) })),
  courses: () => ({ courseGroups: state.courseGroups, courseHref, rooms: state.rooms, seating: state.seatingCharts }),
  shortcuts: () => listShortcuts(),
});
const PALETTE_ACTIONS = [
  ["Upload scanned assignment", "#inbox", "inbox-upload-scans", "Teaching Inbox"],
  ["Create assignment", "#documents?create=1", null, "Assignments"],
  ["Draft a quiz with a model", "#documents?quiz=new", null, "Assignments"],
  ["New seating chart", "#seating", "add-seating", "Seating charts"],
  // Exam records open over Assignments (ASSIGNMENT-WORKSPACE): a bare #exams address now redirects there.
  ["New exam", "#assignments", "add-exam", "Assignments"],
  ["Add event", "#calendar", "add-calendar-event", "Calendar"],
  ["Ask the assistant", "", "assistant", "Faculty assistant (Ctrl/⌘ I)"],
  ["Compose an email", "#mail?compose=1", null, "Mail"],
  ["Message students through Canvas", "#canvas?message=1", null, "Canvas"],
  ["Add a course", "#current-courses", "add-course", "Courses"],
  ["Add a room", "#rooms", "add-room", "Rooms"],
  ["Record an absence", "#absences", "record-absence", "Absences"],
  ["Write a note about a student", "", "student-note", "Student notes"],
  ["Teaching review", "#teaching-review", null, "Find topics to reteach from certified grades"],
  ["Grade delivery", "#grade-delivery", null, "Gradebook, Canvas and Gradescope receipts"],
  ["Student follow-up", "#follow-ups", null, "Conversations and commitments"],
  ["Office hours appointments", "#office-hours", null, "Schedule and record meetings"],
  ["Import accommodations", "#accommodations", "import-accommodations", "Accommodations"],
  ["Import DR exam bookings", "#accommodations", "import-dr-exams", "Accommodations"],
  ["Keyboard shortcuts", "", "shortcuts", "What each key does"],
];

/** Go to `route` (when given), then press the page's own control: its data-action. */
async function perform(route, action) {
  // A route alone is the page's own deep link (e.g. #documents?create=1 opens Create assignment once).
  if (!action) { location.hash = route; return; }
  if (route && routePath(location.hash) !== routePath(route)) {
    const arrived = new Promise((resolve) => {
      window.addEventListener("hashchange", resolve, { once: true });
      setTimeout(resolve, 1500);
    });
    location.hash = route;
    await arrived;
    // A leave guard may have kept the page; then nothing more happens.
    if (routePath(location.hash) !== routePath(route)) return;
    await new Promise((resolve) => requestAnimationFrame(resolve));
  }
  if (action === "shortcuts") { palette.openSheet(); return; }
  if (action === "assistant") { void assistantView.open(); return; }
  // The existing handlers listen for clicks on [data-action]; a transient control reuses them.
  const control = document.createElement("button");
  control.type = "button";
  control.hidden = true;
  control.dataset.action = action;
  document.body.append(control);
  control.click();
  control.remove();
}

// '/' searches the page: the course list, the legacy tools list, or a page's own [data-page-search] box; elsewhere it
// opens the palette. All shortcuts go through ui.js's one registry.
registerShortcut({
  key: "/",
  label: "Search this page, or open the palette",
  run: () => {
    if (isCourseView()) return $("#course-search")?.focus();
    if (["tools", "pinned", "roadmap"].includes(state.view)) return $("#search").focus();
    const own = [...document.querySelectorAll("main [data-page-search]")].find((node) => node instanceof HTMLElement && node.offsetParent !== null);
    if (own instanceof HTMLElement) return own.focus();
    return palette.open();
  },
});
registerShortcut({ key: "Mod+K", label: "Go to a page, course, assignment or action", allowInInput: true, scope: "global", run: () => palette.toggle() });
registerShortcut({ key: "?", label: "Show these keyboard shortcuts", scope: "global", run: () => palette.openSheet() });
$("#dialog").addEventListener("close", () => { delete $("#dialog").dataset.route; });
$("#dialog").addEventListener("cancel", (event) => {
  if ($("#dialog").querySelector('[data-saving="true"]:not([data-closable="true"]), [data-busy="true"]'))
    event.preventDefault();
});
window.addEventListener("hashchange", setView);
window.addEventListener("focus", () => {
  void refresh();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) void refresh();
});
setInterval(() => {
  if (!document.hidden) void refresh();
}, 30000);
setInterval(() => {
  if (!document.hidden && (isCourseView() || state.view === "course") && state.storage.pending_count) void refresh();
}, 3000);
mountWorkspaceNavigation();
mountIcons();
// The rail's status button and popover, and the 'ready for you' / 'finished' notices (compared between overview reads).
systemStatus = mountSystemStatus({ api, overviewStore, toast, refresh: () => refresh(), escape, isOnline: () => online });
setView();
void refresh();
