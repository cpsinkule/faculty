// Home: what needs the instructor now, what is in progress, what is coming, and the workspace's health. A read-only
// projection: every row links to the page that owns the work, and nothing here decides, clears or records anything.
import { assignmentNext, autoGradeLine } from "./grading-overview.js";
import { pipelineMarkup } from "./grading-guide.js";
import { scanNext, currentStage } from "./scan-flow.js";
import { emptyMarkup, loadingMarkup, wallDate, daysFrom } from "./ui.js";
import { patchChildren } from "./dom-patch.js";
import { ASSIGNMENT_BADGES, badgeRoute } from "./page-header.js";
import { TYPE_LABELS, addDays, calendarHref, campusToday, groupFacts, nextEvents } from "./calendar-feed.js";

// Home's static frame (the shell's #overview-content host stays empty until the module mounts it). Kept ids: #home-attention,
// #home-scans (the scans the worker is on, inside Needs you), #home-exams (Coming up), #home-courses, #home-operations and
// the Gradebook shortcut. In progress holds its place from the first paint (rest-shell-6: it used to appear about 1.7 s later
// and push the page down), sized from the overview until Grading answers (`progressPlaceholder`). Create assignment and
// Draft a quiz are the hero's secondary actions (journey-10). The page is two ruled rows of a wide and a narrow column
// (DESIGN-LANGUAGE, the approved Home): Needs you beside Coming up, In progress beside Current courses.
function homeFrame(icon) {
  return `<p id="home-error" class="callout connection-error" data-tone="danger" role="alert" hidden></p>
<div class="home-columns">
  <section class="home-panel home-attention" aria-labelledby="home-attention-title">
    <header class="home-panel-heading"><h2 id="home-attention-title">Needs you</h2></header>
    <div id="home-attention">${loadingMarkup("Checking your workspace…", 2)}</div>
    <div id="home-scans" class="home-working"></div>
  </section>
  <section class="home-panel home-coming" aria-labelledby="home-exams-title">
    <header class="home-panel-heading"><h2 id="home-exams-title">Coming up <small>next 3 weeks</small></h2></header>
    <div id="home-exams">${loadingMarkup("Reading the calendar…", 3)}</div>
    <a class="link-arrow home-panel-more" href="#calendar">Calendar</a>
  </section>
</div>
<div class="home-columns">
  <section id="home-progress-panel" class="home-panel home-progress" aria-labelledby="home-progress-title">
    <header class="home-panel-heading"><h2 id="home-progress-title">In progress</h2><div id="home-progress-links" class="home-panel-links"></div></header>
    <div id="home-progress">${loadingMarkup("Reading grading…", PROGRESS_ROWS)}</div>
  </section>
  <section class="home-panel home-teaching" aria-labelledby="home-courses-title">
    <header class="home-panel-heading"><h2 id="home-courses-title">Current courses</h2></header>
    <div id="home-courses">${loadingMarkup("Loading courses…", 2)}</div>
    <a class="link-arrow home-panel-more" href="#current-courses">All courses</a>
  </section>
</div>
<details id="home-operations" class="home-panel home-operations">
  <summary><span><strong>Workspace health</strong><small id="home-operations-status">Worker, storage, and backups</small></span></summary>
  <div id="home-operations-summary" class="home-operations-summary"></div>
  <div id="home-queues"></div>
  <p class="home-footnote">Worker status comes from its latest heartbeat, which long operations can delay.</p>
</details>
<nav class="home-shortcuts" aria-label="Quick actions"><a class="button" href="#calendar?add=1"><span data-icon="calendar">${icon("calendar")}</span>Add calendar event</a></nav>`;
}

const PROGRESS_ROWS = 10; // In progress's placeholder for several plans: about one row of grading cards (one card is about 300 px).
// In progress's placeholder for one plan nobody has started: the one line it will most likely be ("<title>: grading not started").
const PROGRESS_LINE = `<p class="home-grading-idle" role="status" aria-busy="true">Reading grading…</p>`;

const NEEDS_YOU = new Set(["needs_review", "failed"]);
// The hero's secondary actions: links to the owning page's own windows (each page opens its control and drops the query).
const HERO_SECONDARY = [{label: "Create assignment", icon: "plus", href: "#documents?create=1"}, {label: "Draft a quiz", icon: "spark", href: "#documents?quiz=new"}];
const SHOWN_SCANS = 5; // Scans listed in Needs you and in Working on it; the rest are one link to the Inbox.
const GRADING_EVERY = 120000; // Grading's overview is read after first paint, then at most every two minutes.

/** Count words: plural(3, "scan") → "3 scans". @param {number} count @param {string} word @param {string} [many] */
const plural = (count, word, many = `${word}s`) => `${Number(count).toLocaleString()} ${count === 1 ? word : many}`;

/**
 * The scans and other items that need the instructor, in one list: what it is, its next step, and the page where it is
 * done. Derived from the server's attention cards and its current scans, and, once Home has read Grading, from each
 * assignment's own workflow counts (which then replace the server's light grading counts). Pure.
 * @param {any} data /api/workspace/overview
 * @param {any[] | null} [grading] rows from gradingAttention(), or null while Grading has not been read
 * Each row also says how many things it stands for (`items`: a scan is one, "3 more scans" three, an assignment's row one
 * decision however many responses it holds, as the Assignments badge counts it, 2026-10-18),
 * so the hero's "N things need you" adds up what the badges and the tab title count, not the rows.
 * @returns {{key: string, kind: string, title: string, count: number | null, items: number, detail: string, more?: string, verb: string, href: string, assistant?: string, facts?: {pages: number | null, received: string}}[]}
 */
export function needsYou(data, grading = null) {
  const rows = [];
  const attention = data?.attention || [];
  const isInbox = (/** @type {any} */ item) => item.key === "inbox" || (item.href === "#inbox" && item.derived === true);
  const inbox = attention.find(isInbox);
  const scans = (data?.current_scans || []).filter((/** @type {any} */ job) => NEEDS_YOU.has(job.listed_as));
  for (const job of scans.slice(0, SHOWN_SCANS)) {
    const stages = job.scan?.stages || [], next = scanNext(job.id, stages);
    // Two scans often share a headline ("Unrecognized scan"): the page count and received time tell them apart, never a filename.
    const pages = job.pages?.total ?? job.pages?.done ?? null;
    rows.push({ key: `scan:${job.id}`, kind: job.listed_as === "failed" ? "error" : "review", title: job.scan?.headline || "Scan", count: null, items: 1,
                detail: next.status, more: next.detail, verb: scanVerb(stages, next), href: next.href,
                facts: { pages: pages == null ? null : Number(pages), received: job.created_at || "" } });
  }
  const moreScans = Math.max(0, (inbox?.scans ?? scans.length) - Math.min(scans.length, SHOWN_SCANS));
  if (moreScans) rows.push({ key: "scan:more", kind: "review", title: `${plural(moreScans, "more scan")} need${moreScans === 1 ? "s" : ""} you`, count: null, items: moreScans,
                             detail: "They are listed first under Current in the Teaching Inbox.", verb: "Open Inbox", href: "#inbox" });
  if (inbox?.submissions) rows.push({ key: "inbox:submissions", kind: inbox.submissions_failed ? "error" : "review", title: "Submissions that need you",
                                       count: inbox.submissions, items: inbox.submissions, detail: submissionsDetail(inbox.submissions, inbox.submissions_failed || 0),
                                       verb: "Open Submissions", href: "#inbox?status=submissions" });
  for (const item of attention) {
    if (isInbox(item)) continue;
    if (grading && String(item.key || "").startsWith("grading:")) continue;
    // A `counted_in` card's things are counted by another card (attentionBadges): its row shows its count and adds nothing. A card of
    // an assignment's work is one decision, as the Assignments badge counts it (assignmentDecisions).
    const decision = ASSIGNMENT_BADGES.includes(badgeRoute(String(item.href || "")));
    rows.push({ key: item.key || `${item.href}|${item.label}`, kind: item.kind || "review", title: item.label, count: item.count ?? null,
                items: item.counted_in ? 0 : decision ? 1 : Math.max(1, Number(item.count) || 0), detail: item.detail || "", verb: item.verb || "Open", href: item.href });
  }
  // ASSISTANT "HOME CARD" (2026-10-09): Approve cards waiting in the Assistant's Changes, as the assistant counts them (those of
  // follow-up preparations stay with Student follow-up). Present only when some wait; it opens the drawer's Changes tab.
  const changes = Number(data?.assistant_changes) || 0;
  if (changes) rows.push({ key: "assistant:changes", kind: "review", title: "Assistant changes waiting", count: changes, items: changes,
    detail: `${plural(changes, "change")} the Assistant prepared ${changes === 1 ? "waits" : "wait"} for your Approve.`, verb: "Review changes",
    href: "#overview", assistant: "changes" });
  if (grading?.length) {
    // Grading's rows go where the server's grading cards were: right after the Inbox's.
    const at = rows.findIndex((row) => !row.key.startsWith("scan:") && !row.key.startsWith("inbox:"));
    rows.splice(at < 0 ? rows.length : at, 0, ...grading);
  }
  return rows;
}

// A scan row's button says what the instructor does there, by the stage its next step is in (scanNext names the stage).
const SCAN_VERBS = { scan: "Check pages", assignment: "Choose assignment", students: "Find students", sheets: "Review sheets",
                     papers: "Check papers", review: "Finish review", grading: "Open grading" };
/** @param {any[]} stages @param {ReturnType<typeof scanNext>} next */
function scanVerb(stages, next) {
  const stage = currentStage(stages) || stages.find((item) => item.state === "failed");
  if (!stage) return next.label; // "Open grading" or "Open scan": already verbs.
  if (stage.status === "Working") return "View progress";
  return SCAN_VERBS[stage.key] || "Open scan";
}

/** The submissions row's line: how many wait for review and how many failed, each only when there are some. */
function submissionsDetail(/** @type {number} */ total, /** @type {number} */ failed) {
  const waiting = Math.max(0, total - failed);
  const parts = [waiting ? `${plural(waiting, "submission")} ${waiting === 1 ? "waits" : "wait"} for your review` : "",
                 failed ? `${plural(failed, "submission")} failed` : ""].filter(Boolean);
  return `${parts.join(", ")}.`;
}

/**
 * The grading plans Home speaks for: those of current terms, as the server lists them (`current_grading_plans`), the scope
 * of its own grading cards and the Grading badge. Null (an older server) keeps every plan.
 * @param {any} data /api/workspace/overview
 * @returns {Set<string> | null}
 */
export function currentPlans(data) {
  return Array.isArray(data?.current_grading_plans) ? new Set(data.current_grading_plans) : null;
}

/**
 * In progress's place while Grading is read (it takes a second or more), so Coming up does not move when it answers (rest-shell-6):
 * none when no current term has a grading plan (nothing can show; review fix 2026-10-02: the panel used to appear, then leave);
 * one line for a single plan that no attention card names (an assignment not started is one line); a row of grading cards
 * otherwise, or for an older server that lists no plans. Pure.
 * @param {any} data /api/workspace/overview
 * @returns {"none" | "line" | "cards"}
 */
export function progressPlaceholder(data) {
  const plans = currentPlans(data);
  if (!plans) return "cards";
  if (!plans.size) return "none";
  const named = (data?.attention || []).some((/** @type {any} */ item) => plans.has(item.plan_id) || plans.has(String(item.key || "").split(":")[1]));
  return plans.size === 1 && !named ? "line" : "cards";
}

// Setup steps a paper can wait for: once papers are in, the step the assignment is on is the instructor's to do (journey-5),
// unless the pipeline is working it ("In progress": a preparation, a template reading or the rubric check is running).
const SETUP_STEPS = new Set(["rubrics", "locations", "key"]);
// What the instructor does on that step, by the status the step shows (grading-guide.js rubricProgress, finalizationProgress); a
// status not named here (the answer-area step's "Pending") says the step's own title.
const SETUP_WAITS = {"Needs uploads": "upload the rubrics of every version", "Needs review confirmation": "confirm the rubrics for the rubric review",
  "Needs templates": "confirm the assignment templates", "Needs answer key": "check the answer key of every question",
  "Needs rubric check": "run the rubric check", "Needs approval": "approve the rubrics"};
// An answer-area check that finished and waits for a look (location_status: points match, or a manual review is needed).
const AREAS_WAITING = new Set(["needs_confirmation", "needs_review"]);

// The retired Grading index's place (I7, 2026-10-18): the Assignments index, filtered to the assignments in grading.
export const GRADING_FILTER = "#assignments?filter=grading";

/**
 * Where a grading plan opens from Home: its assignment's shell (with `query`, e.g. "area=collect") when Home knows the assignment
 * and term (`source_assignment_id`, `term_id`), else the plan's own address, which also mounts inside the shell. Pure.
 * @param {any} plan @param {string} [query]
 */
export function planHref(plan, query = "") {
  if (plan?.source_assignment_id && plan?.term_id) {
    return `#assignments/${encodeURIComponent(plan.source_assignment_id)}/${encodeURIComponent(plan.term_id)}${query ? `?${query}` : ""}`;
  }
  return `#grading/${encodeURIComponent(plan?.id || "")}`;
}

/**
 * "Finish the scan checks (N)": the assignments whose scans still wait for their scan checks (Grading's `waiting`). When they are
 * all one assignment Home knows (its `assignment_id` or `source_assignment_id`, and `term_id`), its Collect lists every such scan
 * (I2: `?area=collect&scans=waiting`); otherwise Grading's own link (the one waiting scan, else the Inbox). Pure.
 * @param {any} data /api/grading?health=0
 */
export function scanChecksHref(data) {
  const waiting = Array.isArray(data?.waiting) ? data.waiting : [];
  const pairs = new Set(waiting.map((/** @type {any} */ item) => {
    const aid = item.assignment_id || item.source_assignment_id;
    return aid && item.term_id ? `${aid}\n${item.term_id}` : "";
  }));
  if (waiting.length && pairs.size === 1 && !pairs.has("")) {
    const [aid, term] = [...pairs][0].split("\n");
    return planHref({ source_assignment_id: aid, term_id: term }, "area=collect&scans=waiting");
  }
  return data?.waiting_href || "#inbox";
}

/** One sentence: what grading waits for on a setup step. @param {{status?: string, title: string}} step */
function setupWait(step) {
  const doing = SETUP_WAITS[step.status || ""] || `${step.title.charAt(0).toLowerCase()}${step.title.slice(1)}`;
  return `Grading waits until you ${doing}.`;
}

/**
 * What each grading assignment has waiting for the instructor, from Grading's own overview (the workflow the Grading page
 * shows): responses to review, papers waiting for a version decision, responses whose grading failed, a finished rubric
 * check to review, suggested answer areas to confirm, papers waiting for a setup step (rubrics, answer areas, the key and
 * finalization) and new responses whose grading did not start by itself. The review, failed, finalization and areas keys match
 * the server's cards, which these rows replace. Titles and counts only; only the plans of current terms when `current` is given.
 * The pipeline waits only for the instructor (PIPELINE): these are exactly the steps where it does (journey-5, 2026-10-02).
 * ONE SET OF WORDS, ONE COUNT (2026-10-18): each row is titled with the assignment's needs-you label ("Review grades", "Certify
 * grades", …) and is one decision (`items: 1`) however many responses it holds, as the assignment tabs, the index and the sidebar's
 * Assignments badge count it; its count column still says how many. The links keep their step's deep link, which opens inside
 * the assignment, on the tab that holds it.
 * @param {any} data /api/grading?health=0
 * @param {Set<string> | null} [current] currentPlans() of the overview
 */
export function gradingAttention(data, current = null) {
  /** @type {ReturnType<typeof needsYou>} */
  const rows = [];
  for (const plan of data?.assignments || []) {
    if (current && !current.has(plan.id)) continue;
    const w = plan.workflow || {}, id = plan.id, title = plan.title;
    const base = `#grading/${id}`;
    const start = rows.length;
    if (w.review_responses) rows.push({ key: `grading:${id}:review`, kind: "review", title: `Review grades · ${title}`, count: w.review_responses, items: 1,
      detail: `${plural(w.review_responses, "graded response")} ${w.review_responses === 1 ? "waits" : "wait"} for your decision.`, verb: "Review", href: `${base}?tab=review` });
    if (w.version_decisions) rows.push({ key: `grading:${id}:versions`, kind: "review", title: `Decide paper versions · ${title}`, count: w.version_decisions, items: 1,
      detail: `${w.version_decisions === 1 ? "It is" : "They are"} not graded until you confirm which exam version ${w.version_decisions === 1 ? "it is" : "each is"}.`,
      verb: "Decide versions", href: `${base}?tab=rubrics&focus=versions` });
    if (w.failed) rows.push({ key: `grading:${id}:failed`, kind: "error", title: `Inspect failed grading · ${title}`, count: w.failed, items: 1,
      detail: `${plural(w.failed, "response")} ${w.failed === 1 ? "has" : "have"} no grade because the last attempt failed.`, verb: "Inspect", href: `${base}?tab=review&open=failed` });
    const quality = w.setup?.rubric_quality?.state || plan.rubric_quality?.state;
    if (quality === "needs_review") rows.push({ key: `grading:${id}:finalization`, kind: "review", title: `Review the rubric check · ${title}`, count: null, items: 1,
      detail: "The rubric check finished. Review and approve it before grading.", verb: "Review", href: `${base}?tab=answer-key&open=quality` });
    const areas = (w.setup?.questions || plan.questions || []).filter((/** @type {any} */ q) => AREAS_WAITING.has(q.location_check?.status)).length;
    if (areas) rows.push({ key: `grading:${id}:areas`, kind: "review", title: `Check answer areas and templates · ${title}`, count: areas, items: 1,
      detail: `The suggested answer areas of ${plural(areas, "question")} wait for your look.`, verb: "Check", href: `${base}?tab=locations` });
    // Papers are in, and the step the assignment is on is a setup step: grading waits for the instructor there. A row above that
    // already names this step (versions, the rubric check, answer areas) says it, a failed step has its own queue card, and a step
    // the pipeline is working (review fix, 2026-10-02: a running rubric check read "Papers waiting") needs nobody yet.
    const papersIn = (plan.entry?.cleared_scans || 0) > 0 || (w.collection?.complete || 0) > 0 || (w.new_responses || 0) > 0;
    const step = papersIn && plan.workflow ? assignmentNext(plan, (data.jobs || []).filter((/** @type {any} */ job) => job.assignment_id === id)).current : null;
    const named = {rubrics: "versions", locations: "areas", key: "finalization"}[step?.key || ""];
    const setup = Boolean(step && !step.failed && step.status !== "In progress" && SETUP_STEPS.has(step.key));
    if (setup && !rows.slice(start).some((row) => row.key === `grading:${id}:${named}`)) {
      rows.push({ key: `grading:${id}:setup`, kind: "review", title: `Finish grading preparation · ${title}`, count: null, items: 1,
        detail: setupWait(step), verb: step.action?.label || "Open", href: step.action?.href || step.destination });
    }
    // Every paper graded and reviewed: certifying is the instructor's own decision, the one step the pipeline never takes (usability
    // audit, 2026-10-04: "6 things need you" left it out while In progress said "Next: certify the grades"). Grades changed since a
    // certification reopen it (CERTIFIED-ARCHIVE).
    // READY TO CERTIFY MEANS NOTHING IS STILL COMING (ASSIGNMENT-WORKSPACE, 2026-10-11): not while a linked scan still waits for its
    // scan checks. Certification refuses then, "every paper" would not be true, and the scan's own card says what it waits for.
    if (step?.key === "export" && !step.failed && rows.length === start && !w.awaiting_review) {
      const changed = w.certification?.state === "changed";
      rows.push({ key: `grading:${id}:certify`, kind: "review", title: `Certify grades · ${title}`, count: null, items: 1,
        detail: changed ? "Grades changed after you certified. Certify again to record them in the gradebook."
          : "Every paper is graded and reviewed. Certifying records the grades in the gradebook; nothing is published or sent.",
        verb: "Certify", href: step.action?.href || `${base}?tab=export` });
    }
    // New responses that did not start grading by itself: the switch is off, the last batch was cancelled or reset, or a batch
    // was refused (no models chosen, say). Said on the Grade step; Home says it too, unless a setup step comes first (waiting,
    // working or failed: grading cannot start before it anyway).
    const auto = plan.auto_grade;
    if (!SETUP_STEPS.has(step?.key || "") && w.new_responses && auto && (auto.not_started || auto.paused || !auto.enabled)) {
      rows.push({ key: `grading:${id}:auto`, kind: "review", title: `Grading did not start by itself · ${title}`, count: w.new_responses, items: 1,
        detail: autoGradeLine(plan), verb: "Open Grade", href: `${base}?tab=jobs` });
    }
  }
  return rows;
}


const COMING_DAYS = 21; // Coming up: campus today and the next three weeks.
const COMING_SHOWN = 6;
const COMING_EVERY = 60000; // The calendar feed is read again at most once a minute.

/**
 * Coming up: the next groups of the calendar feed (one exam's rooms or DR sections together), each with its day, how far
 * away it is, what it adds up to, the calendar day and (when there is one) the owning record. Pure; no student data.
 * @param {any[]} events /api/calendar events from campus today on
 * @param {string} today YYYY-MM-DD (campus)
 * @param {number} [count]
 */
export function comingUp(events, today, count = COMING_SHOWN) {
  // The routine weekly schedule (a Term schedule's class meetings and office hours, SCHEDULING) stays on the Calendar, not here.
  return nextEvents((events || []).filter((event) => event.source !== "schedule"), today, count).map((group) => {
    const kind = TYPE_LABELS[group.type] || "Event";
    const source = group.exam_href ? { label: "Exam", href: group.exam_href }
      : group.href ? { label: { seating: "Seating chart", dr: "DR schedule", exam: "Exam", gradebook: "Gradebook" }[group.events[0]?.source] || "Open", href: group.href } : null;
    return {
      key: group.key, type: group.type, date: group.date, title: group.title, kind,
      when: wallDate(group.date), relative: daysFrom(group.date, today),
      facts: [kind, group.code, ...groupFacts(group), group.time ? `${group.time}${group.dr_time ? " DR-center time" : ""}` : ""].filter(Boolean).join(" · "),
      href: calendarHref(group.date), source,
    };
  });
}

/**
 * Coming up's date column: the weekday over the day of the month in the serif, the month under it (a campus wall date,
 * 'YYYY-MM-DD'; never shifted through a time zone). Falls back to the formatted date. Pure.
 * @param {string} date @param {string} when @param {(value: string) => string} escape
 */
export function comingDate(date, when, escape) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(date || ""));
  if (!match) return `<span class="home-coming-date">${escape(when || "")}</span>`;
  const day = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  const part = (/** @type {Intl.DateTimeFormatOptions} */ options) => new Intl.DateTimeFormat("en-US", { ...options, timeZone: "UTC" }).format(day);
  return `<span class="home-coming-date"><span class="sr-only">${escape(when || "")}</span><span class="home-coming-weekday" aria-hidden="true">${escape(part({ weekday: "short" }))}</span><span class="home-coming-day" aria-hidden="true">${escape(String(day.getUTCDate()))}</span><span class="home-coming-month" aria-hidden="true">${escape(part({ month: "short" }))}</span></span>`;
}

/**
 * Working on it's last line: the last seven days' finished scans, linked where the Inbox lists them: History, or Archived for
 * those whose assessment's grades are certified (journey-3: the line said History while every one of them was under Archived).
 * Pure. @param {any} data /api/workspace/overview @param {(value: string) => string} escape
 */
export function finishedMarkup(data, escape) {
  const finished = data?.finished_recent || 0, archived = Math.min(finished, data?.finished_recent_archived || 0), history = finished - archived;
  if (!finished) return "";
  const link = (/** @type {string} */ status, /** @type {string} */ label) => `<a class="link-arrow" href="#inbox?status=${status}">${label}</a>`;
  const done = `${escape(plural(finished, "scan"))} from the last 7 days ${finished === 1 ? "is" : "are"} finished`;
  const text = !archived ? `${done} · ${link("history", "History")}`
    : !history ? `${done}, with certified grades · ${link("archived", "Archived")}`
    : `From the last 7 days: ${escape(plural(history, "scan"))} finished · ${link("history", "History")} · ${escape(archived.toLocaleString())} with certified grades · ${link("archived", "Archived")}`;
  return `<p class="home-finished">${text}</p>`;
}

/** How many things the Needs you rows stand for (their `items`), for the hero's sentence. @param {{items?: number}[] | null} rows */
export function needsCount(rows) {
  return rows ? rows.reduce((sum, row) => sum + (row.items ?? 1), 0) : null;
}

/**
 * The hero's one live sentence: what needs the instructor, the grading assignment furthest along with steps left, and the
 * next thing on the calendar ("2 things need you · Test 1: Certify grades · Next: Test 2 DR exams, Thu, Oct 22"). Pure.
 * @param {{needs?: number | null, grading?: {title: string, label: string} | null, next?: any}} parts
 */
export function daySentence({ needs = null, grading = null, next = null }) {
  const parts = [];
  if (needs !== null) parts.push(needs ? `${needs.toLocaleString()} ${needs === 1 ? "thing needs" : "things need"} you` : "Nothing needs you");
  if (grading) parts.push(`${grading.title}: ${grading.label}`);
  if (next) parts.push(`Next: ${next.title}${next.type === "dr" ? " DR exams" : next.type === "deadline" ? " (deadline)" : ""}, ${next.relative === "today" ? "today" : next.when}`);
  return parts.join(" · ");
}

/**
 * Workspace health in one line, readable while the disclosure is closed. Wording as before: an incomplete backup is never
 * called verified, and a late heartbeat never counts as progress. Pure.
 * @param {any} operations the overview's operations
 * @param {(value: string) => string} date formats an instant
 */
export function healthLine(operations, date) {
  const { worker = {}, totals = {}, storage = {} } = operations || {};
  const workerText = { active: "Worker responding", busy: "Work in progress · heartbeat overdue", stalled: "Worker not responding", stale: "Worker heartbeat overdue", unknown: "Worker has not reported yet" }[worker.state] || "Worker status unavailable";
  const backup = storage.backup || {};
  const backupText = !storage.enabled ? "" : backup.verified_at ? `Backup verified ${date(backup.verified_at)}`
    : ({ disabled: "Backup not configured", not_run: "No verified backup yet", unavailable: "Backup status unavailable" }[backup.status] || `Backup ${String(backup.status || "unknown").replaceAll("_", " ")} · not verified`);
  const disks = storage.disks || [];
  const disk = disks.find((item) => item.state !== "ready") || disks[0];
  const diskText = !disk ? "" : disk.state === "unavailable" ? `${disk.label} unavailable` : `${disk.label} ${bytes(disk.free_bytes)} free${disk.state === "low" ? " (low)" : ""}`;
  const work = [totals.running ? `${totals.running} running` : "", totals.pending ? `${totals.pending} queued` : "", totals.failed ? `${totals.failed} failed` : ""].filter(Boolean).join(", ");
  return [workerText, work, backupText, diskText].filter(Boolean).join(" · ");
}

/** Bytes as GB, or TB from 1,000 GB on. @param {number | null | undefined} value */
function bytes(value) {
  if (value == null) return "Unavailable";
  const gb = value / 1024 ** 3;
  return gb >= 1000 ? `${(gb / 1024).toFixed(1)} TB` : `${gb.toFixed(1)} GB`;
}

/** Home is a read-only projection; existing course and workflow pages own edits. */
export function createHome({api, escape, icon, courseHref, overviewStore = null, header = null, ask = null, toast = null}) {
  const host = document.getElementById("overview-content");
  if (host && !host.hasChildNodes()) host.innerHTML = homeFrame(icon);
  let pending;
  let overview = null;
  let grading = null, gradingAt = 0, gradingPending = null, gradingFailed = false; // gradingFailed: the first read of Grading failed.
  let coming = null, comingDay = "", comingAt = 0, comingPending = null, comingFailed = false;
  let groups = null, showAllQueues = false; // Browser memory only: the queue table's "Show all queues".
  const rendered = new Map();
  // Patched, not rebuilt: a poll that changes one row keeps every other element, focus and open disclosures. A poll that
  // changes nothing changes nothing.
  function patch(id, html) {
    const node = document.getElementById(id);
    if (node && rendered.get(id) !== html) {
      patchChildren(node, html);
      rendered.set(id, html);
    }
  }
  const term = (course) => `${course.term === "Other" ? course.term_other : course.term} ${course.year || ""}`.trim();
  const date = (value) => {
    if (!value) return "Not recorded";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat(undefined, {dateStyle:"medium", timeStyle:"short"}).format(parsed);
  };
  // When a scan arrived: an instant, shown in the browser's clock like every received time.
  const received = (value) => {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? "" : new Intl.DateTimeFormat(undefined, {month:"short", day:"numeric", hour:"numeric", minute:"2-digit"}).format(parsed);
  };
  const duration = (seconds) => seconds == null ? "—" : seconds < 60 ? "Under a minute" : seconds < 3600 ? `${Math.floor(seconds/60)} min` : seconds < 86400 ? `${Math.floor(seconds/3600)} hr` : `${Math.floor(seconds/86400)} days`;
  const onHome = () => document.body?.dataset.workspaceView === "overview";

  // Current courses: each course links to its page, each current section to its own tab, and the term to its gradebook (no
  // painting since 2026-10-04: the instructor, "remove paintings").
  function renderCourses(list) {
    groups = list;
    const current = list.filter(group => group.is_current);
    const code = (group) => `<span class="home-course-code">${escape(group.code)}</span>`;
    patch("home-courses", current.length ? `<ul class="home-course-list" role="list">${current.map((group) => `<li class="home-course"><a class="home-course-main" href="${courseHref(group, null, "current-courses")}">${code(group)}<strong>${escape(group.name)}</strong><span>${escape(term(group))}</span></a><div class="home-course-links"><span class="sr-only">Sections:</span>${group.current_sections.map(section => `<a class="chip home-section-chip" href="${courseHref(group, section, "current-courses")}" aria-label="${escape(`${group.code} section ${section}`)}">${escape(section)}</a>`).join("")}<a class="link-arrow home-course-gradebook" href="#course/${encodeURIComponent(group.id)}/home/gradebook" aria-label="${escape(`${group.code} ${term(group)} gradebook`)}">Gradebook</a></div></li>`).join("")}</ul>`
      : emptyMarkup({icon: "book", title: "Your courses start here", body: "Add a course, then mark the sections you’re teaching now as current.", action: {label: "Add a course", data: {action: "add-course"}}}));
    revealOperations();
    void loadGrading();
    void loadComing();
    paintHeader();
  }

  // One row: what it is (with its count), one line saying what to do, and a button to the page where it is done.
  function needRow(row) {
    // Every row has the count column (empty for one scan or one finalization), so the titles line up.
    const count = row.count == null ? `<span class="home-attention-count" aria-hidden="true"></span>`
      : `<strong class="home-attention-count">${escape(Number(row.count).toLocaleString())}</strong>`;
    const name = row.count == null ? row.title : `${row.count} ${row.title}`;
    const facts = row.facts ? [row.facts.pages ? plural(row.facts.pages, "page") : "", row.facts.received ? `received ${received(row.facts.received)}` : ""].filter(Boolean).join(" · ") : "";
    return `<li class="home-attention-item" data-kind="${escape(row.kind)}" data-need="${escape(row.key)}"${row.count == null ? "" : " data-count"}>${count}<div class="home-need-text"><strong class="home-need-title">${escape(row.title)}</strong>${row.detail ? `<p class="home-need-detail"${row.more ? ` title="${escape(row.more)}"` : ""}>${escape(row.detail)}</p>` : ""}${facts ? `<small class="home-need-facts">${escape(facts)}</small>` : ""}</div><a class="link-arrow home-need-action" href="${escape(row.href)}"${row.assistant ? ` data-assistant-tab-open="${escape(row.assistant)}"` : ""} aria-label="${escape(`${row.verb}: ${name}`)}">${escape(row.verb)}</a></li>`;
  }

  function needRows() { return overview ? needsYou(overview, grading ? gradingAttention(grading, currentPlans(overview)) : null) : null; }

  function renderAttention() {
    const rows = needRows();
    if (!rows) return;
    patch("home-attention", rows.length ? `<ul class="home-attention-list" role="list">${rows.map(needRow).join("")}</ul>`
      : emptyMarkup({icon: "check", title: "Nothing needs you", body: "New reviews and processing problems will appear here."}));
  }

  // Working on it: the scans the worker is on (received time and pages, never a filename), then one line for finished ones.
  function renderWorking(data) {
    const current = data.current_scans || [];
    const working = current.filter(job => !NEEDS_YOU.has(job.listed_as));
    const total = Math.max(working.length, (data.current_scans_total ?? current.length) - current.filter(job => NEEDS_YOU.has(job.listed_as)).length);
    const pages = ({done = 0, total: all = null} = {}) => all ? (done < all ? `${done.toLocaleString()} of ${all.toLocaleString()} pages` : plural(all, "page")) : done ? plural(done, "page") : "";
    const rows = working.slice(0, SHOWN_SCANS).map(job => {
      const facts = [pages(job.pages), job.created_at ? `received ${received(job.created_at)}` : ""].filter(Boolean).join(" · ");
      return `<li><a href="${escape(job.href)}"><strong>${escape(job.scan?.headline || "Scan")}</strong><span>${escape(job.scan?.steps?.[0]?.text || "The worker is on it.")}</span>${facts ? `<small>${escape(facts)}</small>` : ""}</a></li>`;
    });
    const more = total - rows.length;
    if (more > 0) rows.push(`<li><a href="#inbox"><strong>${escape(plural(more, "more scan"))} in progress</strong><span>Open the Teaching Inbox to see ${more === 1 ? "it" : "them"}.</span></a></li>`);
    patch("home-scans", `${rows.length ? `<h3 class="home-subheading">Working on it</h3><ul class="home-records home-working-list" role="list">${rows.join("")}</ul>` : ""}${finishedMarkup(data, escape)}`);
  }

  // Coming up: the calendar feed's next three weeks. Until the feed is read (or when it cannot be), dated exam sittings
  // from the overview stand in. An empty list offers no button of its own: the section's "Calendar" link follows it.
  function renderComing() {
    if (coming) {
      const rows = comingUp(coming, comingDay);
      patch("home-exams", rows.length ? `<ul class="home-coming-list" role="list">${rows.map(row => `<li class="home-coming-item" data-kind="${escape(row.type)}"><a class="home-coming-main" href="${escape(row.href)}">${comingDate(row.date, row.when, escape)}<span class="home-coming-copy"><strong>${escape(row.title)}</strong><small>${escape([row.relative, row.facts].filter(Boolean).join(" · "))}</small></span></a>${row.source ? `<a class="link-arrow home-coming-source" href="${escape(row.source.href)}" aria-label="${escape(`${row.source.label}: ${row.title}`)}">${escape(row.source.label)}</a>` : ""}</li>`).join("")}</ul>`
        : emptyMarkup({icon: "calendar", title: "Nothing in the next 3 weeks", body: "Exams, DR bookings, seating dates and deadlines will appear here."}));
      return;
    }
    if (!overview || !comingFailed) return;
    const sittingDate = (value) => wallDate(value);
    patch("home-exams", overview.upcoming_exams.length ? `<ul class="home-coming-list" role="list">${overview.upcoming_exams.map(exam => `<li class="home-coming-item" data-kind="exam"><a class="home-coming-main" href="${escape(exam.href)}"><span class="home-coming-date">${escape(sittingDate(exam.date))}<small>${escape(exam.time || "Time not set")}</small></span><span class="home-coming-copy"><strong>${escape(exam.title)}</strong><small>${escape([exam.code, exam.term_label, exam.label, exam.location].filter(Boolean).join(" · "))}</small></span></a></li>`).join("")}</ul>`
      : emptyMarkup({icon: "calendar", title: "No dated exams", body: "The calendar could not be read; dated exam sittings appear here."}));
  }

  // The calendar feed from campus today through three weeks: read after first paint, only while Home is on screen, at
  // most once a minute.
  function loadComing(force = false) {
    if (comingPending || !onHome() || document.hidden) return comingPending;
    const today = campusToday();
    if (!force && coming && comingDay === today && Date.now() - comingAt < COMING_EVERY) return null;
    comingAt = Date.now();
    comingPending = (async () => {
      try {
        const data = await api(`/api/calendar?${new URLSearchParams({start: today, end: addDays(today, COMING_DAYS)})}`);
        coming = data.events || []; comingDay = data.today || today; comingFailed = false;
      } catch {
        comingFailed = true; // The overview's dated sittings stand in.
      } finally {
        comingPending = null;
        renderComing();
        paintHeader();
      }
    })();
    return comingPending;
  }

  function render(data) {
    overview = data;
    renderAttention();
    renderWorking(data);
    renderComing();
    renderOperations(data.operations);
    if (grading) renderGrading(grading); // Its scope (current terms' plans) comes with the overview.
    else holdProgress(data);
    document.getElementById("home-error").hidden = true;
    revealOperations();
    void loadGrading();
    void loadComing();
    paintHeader();
  }

  // The hero: the title stays the shell's ("Welcome back, …"); its sentence says what needs the instructor, the grading
  // step furthest along, and what is next on the calendar. Its one primary action uploads scans (the Inbox's own dialog); new
  // work starts beside it (journey-10: Create assignment sat under Workspace health, and Draft a quiz was not on Home).
  function paintHeader() {
    if (!header || !onHome()) return;
    const rows = needRows();
    const plan = grading ? gradingPlans(grading).touched
      .sort((a, b) => b.next.steps.filter(step => step.done).length - a.next.steps.filter(step => step.done).length)[0] : null;
    const next = coming ? comingUp(coming, comingDay, 1)[0] : null;
    // An assignment given but not scanned yet waits for its papers, not for "Grade" (rest-shell-7; `waiting` is assignmentNext's).
    const sentence = daySentence({needs: needsCount(rows), grading: plan ? {title: plan.plan.title, label: plan.next.waiting ? "waiting for papers" : plan.next.label} : null, next});
    // The sentence's first clause ("3 things need you") prints in rust while something needs the instructor.
    header.set({route: location.hash, variant: "hero", subtitle: sentence || undefined, attention: Boolean(needsCount(rows)),
      action: {label: "Upload scanned assignment", action: "inbox-upload-scans", icon: "plus"},
      secondary: HERO_SECONDARY});
  }

  // The current terms' grading assignments with steps left: `touched` ones have a step done, started or failed; `untouched`
  // ones have not begun and are only counted (an assignment nobody started is not work in progress).
  function gradingPlans(data) {
    const current = overview ? currentPlans(overview) : null;
    const plans = (data.assignments || []).filter(plan => !current || current.has(plan.id))
      .map(plan => ({plan, next: assignmentNext(plan, (data.jobs || []).filter(job => job.assignment_id === plan.id))}))
      .filter(({next}) => next.current);
    const touched = plans.filter(({next}) => next.steps.some(step => step.done || step.started || step.failed));
    const idle = plans.filter(entry => !touched.includes(entry));
    return {touched, untouched: idle.length, idle};
  }

  // Until Grading answers, In progress keeps the place the overview says it will take (progressPlaceholder); after a first read
  // that failed it stays away.
  function holdProgress(data) {
    const panel = document.getElementById("home-progress-panel"), place = gradingFailed ? "none" : progressPlaceholder(data);
    if (panel) panel.hidden = place === "none";
    if (place !== "none") patch("home-progress", place === "line" ? PROGRESS_LINE : loadingMarkup("Reading grading…", PROGRESS_ROWS));
  }

  // In progress: each grading assignment under way, as its Grading card states it (the same graph and next step).
  function renderGrading(data) {
    const {touched: plans, untouched, idle: notStarted} = gradingPlans(data);
    const waiting = data.waiting_for_inbox || 0;
    // The Grading index is retired (2026-10-18): "All grading" is the Assignments index's grading filter.
    patch("home-progress-links", `${waiting ? `<a class="link-arrow" href="${escape(scanChecksHref(data))}">Finish the scan checks (${escape(waiting.toLocaleString())})</a>` : ""}<a class="link-arrow" href="${GRADING_FILTER}">All grading</a>`);
    // One assignment not started opens itself (in the assignment shell when Home knows its assignment); several open the
    // Assignments index's grading filter.
    const only = untouched === 1 ? notStarted[0].plan : null;
    const idle = untouched ? `<p class="home-grading-idle">${escape(only ? `${only.title}: grading not started` : `${plural(untouched, "grading assignment")} not started`)} · <a class="link-arrow" href="${escape(only ? planHref(only) : GRADING_FILTER)}">${only ? "Open" : "Assignments"}</a></p>` : "";
    patch("home-progress", `${plans.length ? `<div class="home-grading-grid">${plans.map(({plan, next}) => {
      const when = `${plan.term === "Other" ? plan.term_other : plan.term} ${plan.year || ""}`.trim();
      const done = next.steps.filter(step => step.done).length;
      return `<article class="card home-grading-card" data-assessment="${escape(plan.id)}"><header class="card-head"><h3><a href="${escape(planHref(plan))}">${escape(plan.title)}</a> <span class="home-grading-meta">${escape([plan.code, when, `${done} of ${next.steps.length} steps done`].filter(Boolean).join(" · "))}</span></h3></header><div class="card-body" data-size="compact">${pipelineMarkup(next.steps, {e: escape}, null, `${plan.title} grading steps`)}</div><footer class="card-foot"><p class="home-grading-status">${escape(next.status)}</p><a class="link-arrow" href="${escape(next.href)}">${escape(next.label)}</a></footer></article>`;
    }).join("")}</div>` : ""}${idle}`);
    const panel = document.getElementById("home-progress-panel");
    if (panel) panel.hidden = !plans.length && !waiting && !untouched;
  }

  // Grading's overview costs the server a second or more, so it is read after Home's first paint, only while Home is on
  // screen, and at most every two minutes (or when the tab comes back after that).
  function loadGrading(force = false) {
    if (gradingPending || !onHome() || document.hidden) return gradingPending;
    if (!force && gradingAt && Date.now() - gradingAt < GRADING_EVERY) return null;
    gradingPending = (async () => {
      try {
        if (!grading) await new Promise(resolve => setTimeout(resolve, 250)); // After first paint.
        if (!onHome() || document.hidden) return;
        gradingAt = Date.now();
        grading = await api("/api/grading?health=0");
        gradingFailed = false;
        renderGrading(grading);
        renderAttention();
        paintHeader();
      } catch {
        // Grading may be unavailable (not configured, or its drive is away): Home keeps the server's own counts, and a first
        // read that failed takes In progress's placeholder away.
        if (!grading) {
          gradingFailed = true;
          const panel = document.getElementById("home-progress-panel");
          if (panel) panel.hidden = true;
        }
      } finally {
        gradingPending = null;
      }
    })();
    return gradingPending;
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { void loadGrading(); void loadComing(); } });

  // Workspace health: one line while closed; open, the worker, backup and disks, then only the queues with work in them.
  function renderOperations(operations) {
    const {worker, totals, storage, queues} = operations;
    patch("home-operations-status", escape(healthLine(operations, date)));
    const summary = document.getElementById("home-operations");
    if (summary) summary.dataset.health = worker.state === "stalled" || totals.failed || storage.issue_count || storage.disks.some(disk => disk.state !== "ready") ? "check" : "ok";
    const workerLabel = {active:"Worker responding", busy:"Work in progress · heartbeat overdue", stalled:"Worker not responding · running work may be stuck", stale:"Worker heartbeat overdue", unknown:"Worker has not reported yet"}[worker.state] || "Worker status unavailable";
    const backup = storage.backup;
    const backupLabel = backup.verified_at ? `Verified ${date(backup.verified_at)}` : ({disabled:"Backup not configured", not_run:"No verified backup recorded", unavailable:"Backup status unavailable"}[backup.status] || `Backup ${backup.status.replaceAll("_", " ")}`);
    patch("home-operations-summary", `<div><span class="home-label">Background worker</span><strong>${escape(workerLabel)}</strong><small>Last heartbeat: ${escape(date(worker.heartbeat_at))}</small></div><div><span class="home-label">Latest coordinated backup</span><strong>${escape(backupLabel)}</strong>${backup.error_count ? `<small>${backup.error_count} backup error${backup.error_count === 1 ? "" : "s"}</small>` : ""}</div>${storage.disks.map(disk => `<div><span class="home-label">${escape(disk.label)}</span><strong>${disk.state === "unavailable" ? "Disk unavailable" : `${bytes(disk.free_bytes)} free`}</strong><small>${disk.state === "unavailable" ? "Check the storage connection" : `${disk.free_percent}% available${disk.state === "low" ? " · Running low" : ""}`}</small></div>`).join("")}`);
    const busy = queues.filter(queue => queue.pending || queue.running || queue.needs_review || queue.failed);
    const shownQueues = showAllQueues ? queues : busy;
    const idle = queues.length - busy.length;
    const table = shownQueues.length ? `<div class="workspace-table-wrap" role="region" aria-label="Background processing queues" tabindex="0"><table class="workspace-table"><caption class="sr-only">Background processing queues</caption><thead><tr><th scope="col">Workflow</th><th scope="col">Queued</th><th scope="col">Running</th><th scope="col">Review</th><th scope="col">Failed</th><th scope="col">Oldest queued</th></tr></thead><tbody>${shownQueues.map(queue => `<tr><th scope="row"><a href="${escape(queue.href)}">${escape(queue.label)}</a></th><td>${queue.pending}</td><td>${queue.running}</td><td>${queue.needs_review}</td><td>${queue.failed}</td><td>${duration(queue.oldest_waiting_seconds)}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="home-queues-idle">No queue has work waiting, running or failed.</p>`;
    const toggle = idle ? `<p class="home-queues-more">${showAllQueues ? "Showing every queue" : `${plural(idle, "other queue")} idle`} · <button type="button" class="text-button" data-home-queues aria-expanded="${showAllQueues}">${showAllQueues ? "Show active queues only" : "Show all queues"}</button></p>` : "";
    patch("home-queues", table + toggle + (ask && shownQueues.some(queue => queue.failed || queue.needs_review)
      ? '<button type="button" class="button" data-home-repair>Diagnose and prepare fix</button>' : ''));
  }

  // Delegated, so patched markup needs no rebinding: the queue toggle. (Create assignment is a link to
  // #documents?create=1, which that page opens itself, so every check stays that page's.)
  host?.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const repair = target?.closest('[data-home-repair]');
    if (repair && ask) {
      repair.disabled = true;
      // An error is a toast; the button keeps its label (2026-10-09 review).
      void import('./assistant-recovery.js').then(({recoveryPrompt}) => ask(recoveryPrompt('Workspace health'), {reviewOnly: true, context: {kind: 'recovery', page: '#overview'}}))
        .catch(error => { toast?.(error?.message || 'Could not open the Assistant. Try again.', true); })
        .finally(() => { repair.disabled = false; });
    }
    if (target?.closest("[data-home-queues]")) {
      showAllQueues = !showAllQueues;
      if (overview) renderOperations(overview.operations);
      document.querySelector("[data-home-queues]")?.focus();
    }
  });

  function revealOperations() {
    if (location.hash !== "#overview?operations") return;
    const details = document.getElementById("home-operations");
    if (!details.open) {
      details.open = true;
      details.scrollIntoView({block:"nearest"});
    }
  }

  function showError(error) {
    const node = document.getElementById("home-error");
    node.textContent = `Home could not be refreshed. ${error.message}`;
    node.hidden = false;
  }

  // The shell reads /api/workspace/overview once per refresh (overviewStore) for Home and the sidebar's badges; Home
  // renders from that read. Without a store (tests, older callers) it reads the projection itself.
  overviewStore?.subscribe((data, error) => {
    if (error) showError(error);
    else if (data) render(data);
  });

  async function refresh() {
    if (overviewStore) return overviewStore.refresh();
    if (pending) return pending;
    pending = (async () => {
      try { render(await api("/api/workspace/overview")); }
      catch (error) { showError(error); }
      finally { pending = null; }
    })();
    return pending;
  }

  // The quiet-refresh contract: render() repaints only what changed from the data Home already has; render(true) forgets
  // what was painted and reads the overview, Grading and the calendar again.
  function renderAll(force = false) {
    if (force) {
      rendered.clear();
      void refresh();
      void loadGrading(true);
      void loadComing(true);
    }
    if (groups) renderCourses(groups);
    if (overview) render(overview);
    if (grading) renderGrading(grading);
  }
  return {renderCourses, refresh, render: renderAll};
}
