import {calloutMarkup, disclosureMarkup, emptyMarkup, loadingMarkup, setBusy} from './ui.js';
import {patchChildren} from './dom-patch.js';
import {assignmentDecisionCards, routePath} from './page-header.js';
import {createCollectionReview} from './assignment-collection.js';
import {autoGradeLine} from './grading-overview.js';
import {canvasReceipts} from './teaching-review.js';

// ASSIGNMENT-WORKSPACE: a shell over the owners' editors, never a second workflow engine.
export const AREAS = [['create', 'Create'], ['prepare', 'Prepare'], ['collect', 'Collect'],
  ['grade', 'Grade'], ['certify', 'Certify'], ['return', 'Return']];
export const assignmentHref = (c, area = '') => `#assignments/${encodeURIComponent(c.assignment_id)}/${encodeURIComponent(c.term_id)}${area ? `?area=${area}` : ''}`;
export function areaFor(hash) {
  const [path, query = ''] = hash.replace(/^#/, '').split('?'), args = new URLSearchParams(query);
  if (path.startsWith('assignments/')) return args.get('area') || 'overview';
  if (path.startsWith('grading/')) {
    const tab = args.get('tab') || 'overview';
    return ['rubrics', 'locations', 'answer-key', 'templates', 'rubric-quality'].includes(tab) ? 'prepare'
      : tab === 'export' ? (args.get('open') === 'gradescope' ? 'return' : 'certify') : tab === 'papers' ? 'collect' : 'grade';
  }
  if (path.startsWith('inbox/')) return 'collect';
  if (path.startsWith('grade-delivery/') || path.startsWith('teaching-review/')) return 'return';
  if (path.startsWith('gradebook/') || (path.startsWith('course/') && (args.has('item') || args.has('assignment')))) return 'return';
  if (path.startsWith('documents')) return 'create';
  if (path.startsWith('exams/')) return args.get('tab') === 'collection' ? 'collect' : args.get('tab') === 'grading' ? 'grade' : 'prepare';
  return 'prepare';
}
export function areaHref(c, area) {
  if (c.plan_id && ['prepare', 'grade', 'certify', 'return'].includes(area)) {
    if (area === 'return') return `#grade-delivery/${encodeURIComponent(c.plan_id)}`;
    const tab = {prepare: 'rubrics&workspace=1', grade: 'jobs', certify: 'export'}[area];
    return `#grading/${encodeURIComponent(c.plan_id)}?tab=${tab}`;
  }
  return assignmentHref(c, area);
}
// The addresses assignment_workspace.resolve can place in an assignment. Another working page leaves the shell at once
// rather than being held inside it while its read is pending; the server's answer still decides.
export function mayResolve(hash) {
  const [path, query = ''] = hash.replace(/^#/, '').split('?'), parts = path.split('/'), args = new URLSearchParams(query);
  if (['grading', 'grade-delivery', 'teaching-review', 'exams', 'seating'].includes(parts[0])) return Boolean(parts[1]);
  if (parts[0] === 'exam-day') return parts.length === 3;
  if (parts[0] === 'inbox') return parts[1] === 'job' && Boolean(parts[2]);
  if (parts[0] === 'gradebook' || parts[0] === 'course') return args.has('item') || args.has('assignment');
  if (parts[0] === 'documents') return parts[1] === 'quiz' || ['assignment', 'jobs', 'batch', 'chart'].some(key => args.has(key));
  return parts[0] === 'assignments' && parts.length === 3;
}
const workingViews = new Set(['documents', 'exams', 'exam-day', 'grading', 'inbox', 'seating', 'grade-delivery', 'teaching-review', 'gradebook', 'course']);
const ownerIds = {'grade-delivery': 'teaching-review-section', 'teaching-review': 'teaching-review-section', course: 'course-detail'};
const stateLabel = value => ({idle: 'Not started', started: 'In progress', done: 'Ready', failed: 'Needs attention',
  current: 'Certified', changed: 'Changed since certification', none: 'Not certified'})[value] || 'Not ready';

/**
 * What a row of the index holds against the instructor (requirement, 2026-11-02): its needs-you items, unless the assignment simply has not been started, which is no paper yet and only its setup steps
 * ahead. That row names its next step in muted words, with no orange block and no "more decisions". Inside the assignment the
 * steps stay listed. Pure.
 * @param {any} item a row of `/api/assignments`
 */
export function rowDecisions(item) {
  const needs = item?.needs_you || [];
  const setup = (/** @type {any} */ n) => n.key === 'paper' || n.key === 'preparation' || String(n.key || '').startsWith('ready:');
  return needs.some((/** @type {any} */ n) => n.key === 'paper') && needs.every(setup) ? [] : needs;
}

/**
 * The index's order (usability audit, 2026-10-04: the rows came in the order of their internal IDs): assignments with a decision
 * waiting first, then those with a date by date (paper.exam_date, the exam or Given-on day), then the rest by title as people
 * read it (Exam 2, Exam 3, Final); archived ones last in the same order. The server's list is unchanged. Pure.
 * @param {any} a @param {any} b
 */
export function indexOrder(a, b) {
  const key = (/** @type {any} */ item) => [item.archived ? 1 : 0, rowDecisions(item).length ? 0 : 1, item.paper?.exam_date ? 0 : 1];
  const [x, y] = [key(a), key(b)];
  for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] - y[i];
  return String(a.paper?.exam_date || '').localeCompare(String(b.paper?.exam_date || ''))
    || String(a.title || '').localeCompare(String(b.title || ''), undefined, {numeric: true, sensitivity: 'base'});
}

/**
 * INDEX TABS (requirement, 2026-10-09, in place of the Status select): Pending holds a current term's assignments from creation until
 * their grades are certified, and takes a certified one back while its certification no longer holds (changed, new work since, an
 * open regrade request: CERTIFIED-ARCHIVE's view, `archived`); Certified holds the current term's assignments that view keeps;
 * Archived holds every assignment of an earlier term, by course, whatever its state. Pure.
 */
export const INDEX_TABS = [['pending', 'Pending'], ['certified', 'Certified'], ['archived', 'Archived']];
export const indexTab = (/** @type {any} */ item) => !item.is_current ? 'archived' : item.archived ? 'certified' : 'pending';
// Earlier links named the Status select's values: Current is Pending, and "all" came only from a past term's course page.
export const tabFromStatus = (/** @type {string | null} */ status) =>
  INDEX_TABS.some(([key]) => key === status) ? status : ({current: 'pending', all: 'archived'})[status || ''] || null;
// The Courses page's order of the terms of one year, latest first.
const TERM_ORDER = {Fall: 6, 'Summer II': 5, 'Summer (Full)': 4, 'Summer I': 3, Spring: 2, Other: 1};

/**
 * Archived, stratified by course (requirement, 2026-10-09): one group per course-wide definition, by code as read (MATH 140 before
 * MATH 1000); inside it the latest term first, then the index's order. Pure.
 * @param {any[]} rows
 */
export function archiveGroups(rows) {
  const groups = new Map();
  for (const row of rows) {
    if (!groups.has(row.definition_id)) groups.set(row.definition_id, {definition_id: row.definition_id, code: row.code, rows: []});
    groups.get(row.definition_id).rows.push(row);
  }
  const latest = (/** @type {any} */ a, /** @type {any} */ b) => (b.year || 0) - (a.year || 0)
    || (TERM_ORDER[b.term] || 0) - (TERM_ORDER[a.term] || 0) || String(a.term_other || '').localeCompare(String(b.term_other || ''))
    || indexOrder(a, b);
  return [...groups.values()].map(group => ({...group, rows: group.rows.sort(latest)}))
    .sort((a, b) => String(a.code || '').localeCompare(String(b.code || ''), undefined, {numeric: true, sensitivity: 'base'}));
}

/**
 * Where a row's title leads: a certified assignment (CERTIFIED-ARCHIVE's view, so never one back in Pending) opens its Certify page on
 * every student's grades (requirement, 2026-10-09); any other
 * opens its overview. Pure.
 * @param {any} item a row of `/api/assignments`
 */
export const rowHref = (/** @type {any} */ item) => item.archived && item.plan_id ? `${areaHref(item, 'certify')}&open=students` : item.href;

/**
 * What a row says beside its title. A certified one gives the day it was certified; one whose certification no longer holds says
 * why it is back in Pending, in the certification's own words (`stateLabel`); anything else is the server's summary. Pure.
 * @param {any} item a row of `/api/assignments` @param {(iso: string) => string} day
 */
export function rowStatus(item, day) {
  if (item.archived) return [`Grades certified${item.archived.certified_at ? ` · ${day(item.archived.certified_at)}` : ''}`];
  const needs = item.unavailable?.length ? [] : item.needs_you || [];
  const task = needs[0];
  const summary = task ? `${task.label}${task.count == null ? '' : ` · ${task.count} ${task.unit || ''}`}`.trim()
    : item.summary || 'Prepare the paper and grading', state = item.workflow?.certification?.state;
  if (state === 'changed') return ['Changed since certification', summary];
  if (state === 'current' && !item.unavailable?.length) return ['Certified · new work since', summary];
  return [summary];
}

/**
 * Each tab's mark (ASSIGNMENT-WORKSPACE "ONE NAVIGATION"; DESIGN-LANGUAGE §1.8, lifecycle tabs show done, needs you and current):
 * `needs` with how many decisions wait there (the needs-you items the overview lists, by the tab they open), `failed`, `done`, or
 * '' (not started or under way). From the overview's read alone (stages, needs-you items, grading summary). Pure.
 * @param {any} v the assignment's read (`/api/assignments/<assignment>/<term>`)
 */
export function stageMarks(v) {
  if (!v?.lifecycle) return {};
  const needs = v.needs_you || [], w = v.workflow || {};
  const stage = (/** @type {string} */ key) => (v.lifecycle.stages || []).find((/** @type {any} */ s) => s.key === key)?.state || 'idle';
  const waiting = (/** @type {string[]} */ ...keys) => needs.filter((/** @type {any} */ n) => keys.some(k => n.key === k || n.key.startsWith(`${k}:`))).length;
  const readiness = area => needs.filter(n => n.key.startsWith('ready:') && areaFor(n.href || '') === area).length;
  const mark = (/** @type {number} */ count, /** @type {string} */ otherwise) => count ? {state: 'needs', count} : {state: otherwise, count: 0};
  const review = w.progress?.review || {}, cert = w.certification?.state || 'none';
  const graded = Boolean(review.total) && review.completed === review.total && !w.pending && !w.new_responses;
  return {
    overview: mark(needs.length, ''),
    create: mark(waiting('paper', 'makeup') + readiness('create'), [stage('paper'), stage('print')].includes('failed') ? 'failed' : stage('paper') === 'done' && stage('print') === 'done' ? 'done' : ''),
    prepare: mark(waiting('preparation', 'answer-areas', 'version_decisions') + readiness('prepare'), v.plan_id && !w.unavailable ? 'done' : ''),
    collect: mark(waiting('scan', 'upload'), stage('collected') === 'failed' ? 'failed' : stage('collected') === 'done' ? 'done' : ''),
    grade: mark(waiting('review_responses', 'failed', 'grading-unavailable'), w.failed ? 'failed' : graded ? 'done' : ''),
    // A changed certification is ONE thing to do, whether or not the server already lists it as the `certify` item (it does once every
    // response is reviewed): the tab said 2 for it (2026-10-11).
    certify: mark(Math.max(waiting('certify'), cert === 'changed' ? 1 : 0), cert === 'current' ? 'done' : ''),
    return: mark(waiting('regrades'), ''), // Recorded locally is not evidence of delivery or publication.
  };
}
const markWords = {needs: 'waits for you', failed: 'needs attention', done: 'done'};

/** QUIZZES "SHARED STAMPS": one line per stamp that came back on more than one paper, naming whose each paper is. */
export function sharedStampLines(stamps) {
  return (stamps || []).map(stamp => ({
    label: `Copy ${stamp.copy_number}: ${stamp.papers.map(paper => paper.student || 'name not read yet').join(' and ')}`,
    scan: stamp.papers.find(paper => !paper.student)?.job_id || stamp.papers[stamp.papers.length - 1].job_id,
  }));
}
export const SHARED_STAMPS_NOTE = 'This only catches copying when both papers come back: a photocopy handed in alone reads like the original. A paper scanned twice shows here too. The papers are still graded.';

/** Recent activity with each run of the same event said once, with how many and the latest time ("Gradescope created ×4"). Pure.
 * @param {any[]} activity newest first */
export function activityRuns(activity) {
  const runs = [];
  for (const a of activity || []) {
    const label = a.label || String(a.kind || '').replaceAll('_', ' ');
    const last = runs[runs.length - 1];
    if (last && last.label === label) last.count += 1;
    else runs.push({label, count: 1, created_at: a.created_at});
  }
  return runs;
}

/** Where the grades went, in plain words (the delivery receipts the overview read), or null when they were not read. Canvas is
 * named once a gradebook column holds them (2026-10-11: an assessment never sent to Canvas read like one that was): not sent, sent,
 * or sent with how many grades Return's own line says to check (`canvasReceipts`). Pure. */
export function deliveryText(delivery) {
  if (!delivery) return null;
  if (delivery.unavailable) return 'Could not be read';
  const columns = delivery.columns?.length || 0, published = delivery.publications?.filter(p => p.status === 'complete').length || 0;
  const canvas = canvasReceipts(delivery.columns);
  return [columns ? `In ${columns} gradebook column${columns === 1 ? '' : 's'}` : 'Not in a gradebook column yet',
    !columns ? '' : canvas.open === canvas.never && !canvas.accepted ? 'not sent to Canvas' : canvas.open ? `sent to Canvas, ${canvas.open} to check` : 'sent to Canvas',
    published ? `published on Gradescope${published > 1 ? ` (${published} times)` : ''}` : 'not published on Gradescope'].filter(Boolean).join(' · ');
}

/**
 * WHY GRADING WAITS (requirement, 2026-10-11): beside "N responses ready for grading" the overview says, in the Grade step's own words
 * (`autoGradeLine`), that they start by themselves, or why they do not: automatic grading is off, the last batch was cancelled or
 * reset, or a batch could not start. Those three are the instructor's (`yours`, as on Home) and open the Grade step, where a batch
 * is started. While grading preparation is listed under Needs you, a question it covers holds its responses, so nothing promises
 * a start. Null when no response waits or the state was not read. Pure.
 * @param {any} v the assignment's read
 */
export function gradingWait(v) {
  const w = v?.workflow || {}, auto = v?.auto_grade;
  if (!w.new_responses || !auto || !v.plan_id) return null;
  const yours = Boolean(!auto.enabled || auto.paused || auto.not_started);
  if (!yours && (v.needs_you || []).some((/** @type {any} */ n) => n.key === 'preparation')) {
    return {text: 'A question still in preparation holds its responses; the others start by themselves.', yours: false, href: ''};
  }
  const text = autoGradeLine({auto_grade: auto, workflow: w});
  return text ? {text, yours, href: yours ? `#grading/${encodeURIComponent(v.plan_id)}?tab=jobs` : ''} : null;
}

export const AUTOMATIONS = [['auto_grade', 'grading'], ['auto_flag_review', 'flag review'], ['auto_certify', 'late papers certify'],
  ['auto_gradescope', 'Gradescope after Certify']];
/**
 * What runs by itself for an assignment (2026-10-18 review item 9), from the Compute settings' own switches (`/api/grading/settings`):
 * automatic grading, the flag review, late papers certifying themselves, and Gradescope after Certify. One muted line on Readiness
 * that opens those settings; null while they were not read. Pure.
 * @param {any} settings the `settings` object of `/api/grading/settings`
 */
export function automationLine(settings) {
  if (!settings) return null;
  const on = AUTOMATIONS.filter(([key]) => settings[key] !== false).map(([, words]) => words);
  return on.length ? `Runs by itself: ${on.join(', ')}` : 'Nothing runs by itself: you start each step';
}

/**
 * ONE COUNT OF MISSING PAPERS (2026-10-18): "without a scanned paper" is the record's students with no scan at all (`no_scan`), and
 * only once any paper of it is back; an incomplete paper (partial, missing pages) is counted apart. Collect's lead says it, plainly;
 * nothing asks for them (THE ZEROS ARE SAID AT CERTIFY, 2026-11-05). Pure.
 * @param {any} c the grading summary's `collection` counts
 */
export function missingPapers(c) {
  const back = c ? (c.complete || 0) + (c.partial || 0) + (c.needs_review || 0) + (c.missing_pages || 0) : 0;
  return {back, without: back ? c.no_scan || 0 : 0, incomplete: back ? (c.partial || 0) + (c.missing_pages || 0) : 0};
}

/**
 * The assignment's scans still in their scan checks (`inbox_ready` false in the grading summary the overview already read), each with
 * the scan page its first open check is on (the needs-you item's address), else the scan itself. Pure.
 * @param {any} v the assignment's read
 */
export function waitingScans(v) {
  return (v?.scans || []).filter((/** @type {any} */ s) => s.inbox_ready === false).map((/** @type {any} */ s) => {
    const item = (v.needs_you || []).find((/** @type {any} */ n) => String(n.key).startsWith(`scan:${s.job_id}:`));
    return {key: s.job_id, label: s.filename || s.overview?.headline || 'Scan', href: item?.href || `#inbox/job/${encodeURIComponent(s.job_id)}`,
      detail: item?.label || s.overview?.headline || '', pages: s.pages};
  });
}

/**
 * Before grading is prepared for an assignment (no plan), what Grade, Certify and Return wait for, in one line each (2026-10-18 review
 * item 12: they all showed Prepare's panel). Prepare itself offers Prepare grading. Pure.
 */
export const WAITS_FOR_PLAN = {
  grade: 'Grading starts once grading is prepared for this assignment.',
  certify: 'Certify waits for graded and reviewed papers; grading is not prepared yet.',
  return: 'Return waits for certified grades.',
};

export function createAssignmentWorkspace({api, escape: e, header, documents, toast, showDialog, dialogHeader, ask, overviewStore = null}) {
  let generation = 0, route = '', context = null, ownerSpec = null, timer = null, mounted = null, value = null;
  let indexValue = null, pendingAction = '', pending = false, held = null, provisional = false, shownHeader = null;
  // I1: `?area=collect&upload=1` opens Collect's Upload scans once (the address drops it first). `automation`: the Compute settings
  // read when the overview opens (not by its quiet refresh), for Readiness's "Runs by itself" line.
  let pendingUpload = '', pendingMakeup = '', automation = null, announced = '', waitingShownFor = '';
  // The tabs' marks and the assignment they were read for (a working page reads them in the background, at most every 15 s).
  let marks = {}, marksFor = '', marksAt = 0;
  const pairKey = (/** @type {any} */ c) => c ? `${c.assignment_id}/${c.term_id}` : '';
  const root = () => document.querySelector('#assignment-workspace');
  const body = () => root().querySelector('[data-assignment-body]');
  const parents = new Map();
  // `tab` is the index's tab (INDEX_TABS); the other three are its filters, and every tab's count follows them.
  const filters = {query: '', stage: '', tab: 'pending', course: ''};
  const enc = encodeURIComponent;
  const collectionReview = createCollectionReview({api, escape: e, toast});
  function startAssignment() {
    showDialog(`${dialogHeader('Create assignment', 'Choose how you want to begin.')}<div class="dialog-body assignment-start"><button class="button primary" data-start-paper>Create paper and print</button><p>Use LaTeX, registered paper or labels for paper printed elsewhere.</p><button class="button" data-start-draft>Draft a quiz with a model</button><p>Review its questions, versions and rubrics before approval.</p><button class="button" data-action="add-exam">Arrange an existing assessment</button><p>Choose its assignment, sections and sittings.</p></div>`);
    document.querySelector('[data-start-paper]').onclick = () => { document.querySelector('#dialog').close(); documents().create(filters.course); };
    document.querySelector('[data-start-draft]').onclick = () => { document.querySelector('#dialog').close(); documents().draft(filters.course); };
  }
  const href = (url, label, cls = 'link-arrow') => `<a class="${cls}" href="${e(url)}">${e(label)}</a>`;
  const section = (title, content, attrs = '') => `<section class="assignment-block"${attrs}><h2>${e(title)}</h2>${content}</section>`;
  const list = items => `<ul class="assignment-actions">${items.map(item => `<li data-patch-key="${e(item.key || item.href)}">${item.count == null ? '' : `<span><strong>${e(String(item.count))}</strong> ${e(item.unit || '')}</span>`}${href(item.href, item.label)}</li>`).join('')}</ul>`;
  // Recent activity's time: a date and a minute, in the browser's time zone (an instant, not a campus wall date).
  const when = new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'});
  // The day grades were certified, also an instant (`frq_certifications.created_at`).
  const certifiedDay = new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric'});

  function restoreOwner() {
    if (mounted) { parents.get(mounted.id)?.append(mounted); mounted = null; }
  }
  function mountOwner(view) {
    const node = document.querySelector(`#${ownerIds[view] || `${view}-section`}`);
    if (!node || mounted === node) return;
    restoreOwner();
    if (!parents.has(node.id)) parents.set(node.id, node.parentElement);
    root().querySelector('[data-assignment-editor]').append(node); mounted = node;
  }
  function shell() {
    if (root().querySelector('[data-assignment-nav]')) return;
    root().innerHTML = '<nav class="assignment-nav" aria-label="Assignment workspace" data-assignment-nav></nav><div data-assignment-error role="status"></div><div data-assignment-body></div><div data-assignment-editor></div>';
    // INBOX "pending context reads": a held navigation is never followed, so no action can use the former assignment.
    navNode().addEventListener('click', event => {
      if (pending && event.target.closest('a')) { event.preventDefault(); event.stopPropagation(); }
    }, true);
  }
  const navNode = () => root().querySelector('[data-assignment-nav]');
  // I4: the shell's assignment context appeared, changed or cleared (the sidebar keeps a scan page inside it under Assignments).
  function announce() {
    const key = pairKey(context);
    if (key === announced) return;
    announced = key;
    window.dispatchEvent(new CustomEvent('faculty:assignment-context', {detail: {active: Boolean(context),
      ...(context ? {assignment_id: context.assignment_id, term_id: context.term_id} : {})}}));
  }
  // No assignment on screen: `data-area` goes with it (a scan page outside an assignment keeps its own header), and that is announced.
  function cleared() {
    if (context) return;
    delete root()?.dataset.area;
    announce();
  }
  function nav() {
    const selected = areaFor(location.hash);
    // ONE NAVIGATION: the area on screen, for the owner pages' own navigation to follow (assignment-workspace.css).
    if (context) root().dataset.area = selected; else delete root().dataset.area;
    announce();
    const items = context ? [['overview', 'Needs you & progress'], ...AREAS] : [];
    navNode().removeAttribute('aria-busy');
    const shown = marksFor === pairKey(context) ? marks : {};
    // A tab's mark is a hidden child drawn by CSS from data-mark/data-count, in a slot every tab keeps, so the tabs never move when
    // the marks arrive and a tab's name and text stay its word (assignment-workspace.css). Its state is the tab's description
    // (aria-describedby, a hidden line read after the name) and its title.
    patchChildren(navNode(), items.map(([key, label]) => {
      const m = shown[key] || {state: '', count: 0};
      const said = m.state ? (m.state === 'needs' ? `${m.count} ${m.count === 1 ? 'decision waits' : 'decisions wait'} for you` : markWords[m.state]) : '';
      return `<a href="${e(key === 'overview' ? assignmentHref(context) : areaHref(context, key))}" data-patch-key="tab-${key}"${selected === key ? ' aria-current="page"' : ''}${m.state ? ` data-mark="${m.state}" title="${e(`${label}: ${said}`)}" aria-describedby="assignment-mark-${key}"` : ''}>${e(label)}<span class="assignment-mark" aria-hidden="true"${m.count ? ` data-count="${m.count}"` : ''}></span></a>${m.state ? `<span id="assignment-mark-${key}" data-patch-key="mark-${key}" hidden>${e(said)}</span>` : ''}`;
    }).join(''));
  }
  // While a working page of an assignment is open, its marks are read again every 30 s (loadMarks keeps to one read per 15 s), so a
  // decision made on the page (certifying, say) clears its tab's mark without a move between tabs; never while hidden or in a dialog.
  let markTimer = null;
  function watchMarks() {
    clearTimeout(markTimer);
    markTimer = setTimeout(() => {
      const c = context, view = location.hash.slice(1).split('/')[0];
      if (!c || !workingViews.has(view)) return;
      if (!document.hidden && !document.querySelector('#dialog')?.open) void loadMarks(c);
      watchMarks();
    }, 30000);
  }
  // Reads the assignment's state for the tabs while a working page is on screen; the overview's own read marks them directly.
  async function loadMarks(/** @type {any} */ c) {
    const key = pairKey(c);
    if (!key || (marksFor === key && Date.now() - marksAt < 15000)) return;
    try {
      const read = await api(`/api/assignments/${enc(c.assignment_id)}/${enc(c.term_id)}?delivery=0`);
      if (pairKey(context) !== key) return;
      marks = stageMarks(read); marksFor = key; marksAt = Date.now(); nav();
    } catch { /* the tabs stay unmarked; the page itself is unaffected */ }
  }
  // Ends a held move between assignment pages; `clear` also takes the former assignment's navigation away.
  function release(clear = true) {
    const wasProvisional = provisional;
    pending = false; held = null; provisional = false;
    if (clear && navNode()) { navNode().innerHTML = ''; navNode().removeAttribute('aria-busy'); }
    return wasProvisional;
  }
  function assignmentHeader(c, spec = null) {
    const area = areaFor(location.hash), label = AREAS.find(([key]) => key === area)?.[1] || 'Needs you & progress';
    // The owner's context line stays when it extends this course and term: it names the object on screen (a chart's
    // sections, a scan's file, the exam-day packet), which the title, now the assignment's, no longer carries.
    const base = `${c.code} · ${c.term_label}`;
    const scope = typeof spec?.context === 'string' && spec.context.startsWith(base) ? spec.context : base;
    // No subtitle that only repeats what the tabs or the title say (the area's name, an owner's subtitle that is the
    // assignment's title): every area then opens with the same header, and the tabs never move (2026-10-04 review: the bar
    // sat 61 px lower on Needs you, Create, Collect and Return than on Prepare, Grade and Certify). An owner's own sentence stays.
    const subtitle = spec?.subtitle && spec.subtitle !== c.title ? spec.subtitle : '';
    return {...(spec || {}), route: location.hash, title: c.title,
      context: scope, label: scope, subtitle,
      crumbs: [{label: 'Home', href: '#overview'}, {label: 'Assignments', href: '#assignments'},
        {label: c.title, href: assignmentHref(c)}, {label}],
      ...(spec ? {} : {action: null, secondary: []})};
  }
  function paintHeader() {
    if (!context || route !== location.hash) return;
    header.set(shownHeader = assignmentHeader(context, ownerSpec));
  }
  // While a move resolves, the header keeps the shape it had (its subtitle, its buttons shown but disabled), so the page
  // below it does not shift; no button can act for the former page.
  function heldHeader(previous) {
    const fresh = assignmentHeader(previous), shape = shownHeader?.title === previous.title ? shownHeader : fresh;
    const inert = item => ({label: item.label, icon: item.icon, disabled: true});
    header.set({...shape, route: location.hash, crumbs: fresh.crumbs,
      action: shape.action ? inert(shape.action) : null, secondary: (shape.secondary || []).map(inert)});
  }
  // The owner's header once its address is resolved: no context means the owner's own header (or the shell's defaults).
  function ownerHeader(wasProvisional) {
    if (ownerSpec) header.set(ownerSpec);
    else if (wasProvisional) header.reset();
  }
  // An owner's own address changes (a grading step, Review's question and response written into the address) stay at the
  // assignment the route resolved to whenever that resolve reads the path alone; documents and gradebook links also read
  // their query. Context, navigation and the owner's header then stay: re-resolving painted the shell's own subtitle until
  // the owner's next header, and the page jumped by its height (a Refresh at the end of Review moved the scroll 61 px).
  const samePlace = (a, b) => {
    const path = routePath(a), view = path.split('/')[0];
    return path === routePath(b) && workingViews.has(view) && !['documents', 'gradebook', 'course'].includes(view);
  };
  const editorHeader = {set(spec) {
    if (!spec || routePath(spec.route) !== routePath(location.hash)) return false;
    ownerSpec = spec;
    if (context && !pending && route !== location.hash && samePlace(location.hash, route)) { route = location.hash; nav(); }
    // While a move between assignment pages resolves, the assignment's header stays; the owner's is applied after.
    if (pending && route === location.hash) return true;
    if (route === location.hash && context) paintHeader();
    else header.set(spec);
    return true;
  }};
  function schedule(view) {
    clearTimeout(timer);
    // Only our read-only summary polls. The mounted editors keep their own refresh and leave guards.
    if (view !== 'assignments') return;
    timer = setTimeout(() => {
      if (route !== location.hash) return;
      if (document.hidden || document.querySelector('#dialog')?.open) { schedule(view); return; }
      void render(view, true, true);
    }, 30000);
  }
  // INBOX / ASSIGNMENT-WORKSPACE: association changes refresh only this shell.
  // The owning scan keeps its mounted nodes, focus, scroll and unfinished inputs.
  document.addEventListener('faculty:scan-association-changed', event => {
    const job = location.hash.split('?')[0].match(/^#inbox\/job\/([^/]+)(?:\/|$)/)?.[1];
    if (!job || job !== event.detail?.jobId || (context && context.plan_id === event.detail.planId)) return;
    // A scan page announces its association when it mounts. During a held move the navigation is already inert:
    // read the address again (the newer read supersedes one that may predate the change) and keep it held.
    if (pending) { void render('inbox', true); return; }
    context = null;
    nav();
    if (ownerSpec) header.set(ownerSpec);
    void render('inbox', true);
  });
  document.addEventListener('faculty:gradebook-association-changed', event => {
    if (event.detail?.route !== location.hash) return;
    const view = location.hash.slice(1).split('/')[0];
    if (!['gradebook', 'course'].includes(view)) return;
    if (pending) { void render(view, true); return; }
    context = null;
    nav();
    if (ownerSpec) header.set(ownerSpec);
    void render(view, true);
  });
  // The creation window reports what it made once it closes (documents.js); only the list on screen is read again.
  document.addEventListener('faculty:assignments-changed', () => {
    if (route === location.hash && location.hash.split('?')[0] === '#assignments') void render('assignments', true);
  });
  // One assignment's row; the whole row opens it by its title (`rowHref`: a certified one on its grades). Under a course's heading
  // (Archived) the title is a level lower and the row names only its term.
  function rowMarkup(item, level = 'h2', course = true) {
    const lines = rowStatus(item, iso => certifiedDay.format(new Date(iso))), decisions = rowDecisions(item);
    return `<article class="assignment-row" data-patch-key="${e(item.assignment_id + item.term_id)}"${decisions.length && !item.archived ? ' data-waits="you"' : ''}>
      <div><${level}><a href="${e(rowHref(item))}">${e(item.title)}</a></${level}><p>${course ? `${e(item.code)} · ` : ''}${e(item.term_label)}</p></div>
      <div>${lines.map(line => `<p>${e(line)}</p>`).join('')}
      ${decisions.length ? `<p class="assignment-row-waits">Needs you${decisions.length > 1 ? ` · ${decisions.length - 1} more decision${decisions.length === 2 ? '' : 's'}` : ''}</p>` : ''}</div></article>`;
  }
  const emptyTab = {
    pending: {title: 'Nothing pending', body: 'Assignments of the current term stay here from creation until their grades are certified.'},
    certified: {title: 'Nothing certified yet', body: 'An assignment moves here once its grades are certified, and back to Pending if anything changes.'},
    archived: {title: 'No earlier terms', body: 'Assignments of past terms are kept here, by course.'},
  };
  // What the Assignments badge counts, by name, at the top of the index: the badge's own cards (`assignmentDecisionCards`), one row
  // each, whatever the tab and filters show (requirement, 2026-11-02). Nothing is drawn when nothing waits.
  function drawNeeds() {
    const c = root()?.querySelector('[data-assignment-needs]');
    if (!c) return;
    const cards = assignmentDecisionCards(overviewStore?.get()?.attention);
    patchChildren(c, cards.length ? section('Needs you', `<ul class="assignment-actions">${cards.map(card =>
      `<li data-patch-key="${e(card.key || card.href)}"><span><strong>${e(Number(card.count).toLocaleString())}</strong> ${e(card.label)}${card.detail ? `<small>${e(card.detail)}</small>` : ''}</span>${href(card.href, card.verb || 'Open')}</li>`).join('')}</ul>`, ' data-waits="you" data-patch-key="needs"') : '');
  }
  overviewStore?.subscribe(drawNeeds);
  function drawIndex() {
    const c = root().querySelector('[data-assignment-list]');
    if (!c || !indexValue) return;
    drawNeeds();
    const query = filters.query.toLocaleLowerCase();
    const shown = indexValue.assignments.filter(item => (!filters.course || item.definition_id === filters.course)
      && (!filters.stage || (item.filters || []).includes(filters.stage))
      && `${item.title} ${item.code} ${item.term_label}`.toLocaleLowerCase().includes(query));
    // Unapproved quiz drafts are Pending's (preparation work), listed ahead of its assignments.
    const drafts = filters.stage && filters.stage !== 'preparation' ? [] : indexValue.drafts.filter(d => (!filters.course || d.definition_id === filters.course)
      && `${d.title} ${d.course}`.toLocaleLowerCase().includes(query));
    // Each tab counts what it would list under the same search, work and course (an inventory figure, never the needs-you block).
    const counts = Object.fromEntries(INDEX_TABS.map(([key]) => [key, shown.filter(item => indexTab(item) === key).length + (key === 'pending' ? drafts.length : 0)]));
    patchChildren(root().querySelector('[data-assignment-tabs]'), INDEX_TABS.map(([key, label]) =>
      `<button type="button" data-assignment-tab="${key}" aria-pressed="${filters.tab === key}">${label} <b>${counts[key].toLocaleString()}</b></button>`).join(''));
    const rows = shown.filter(item => indexTab(item) === filters.tab).sort(indexOrder);
    const markup = filters.tab === 'archived'
      ? archiveGroups(rows).map(group => section(group.code, group.rows.map(item => rowMarkup(item, 'h3', false)).join(''),
        ` data-patch-key="course-${e(group.definition_id)}"`)).join('')
      : rows.map(item => rowMarkup(item)).join('');
    const listed = filters.tab === 'pending' ? drafts : [];
    const empty = filters.query || filters.stage || filters.course
      ? {title: 'No assignments match', body: 'Change the filters or choose another tab.', kind: 'filtered'} : emptyTab[filters.tab];
    patchChildren(c, markup || (listed.length ? '' : emptyMarkup(empty)));
    patchChildren(root().querySelector('[data-assignment-drafts]'), listed.map(d =>
      `<article class="assignment-row" data-patch-key="${e(d.id)}"><div><h2><a href="#documents/quiz/${enc(d.id)}">${e(d.title)}</a></h2><p>${e(d.course)} · ${e(d.term)}</p></div><p>${e(({ready: 'Ready for your review', drafting: 'Drafting', failed: 'Draft needs attention', approving: 'Preparing approved assignment'})[d.state] || 'Draft')}</p></article>`).join(''));
  }
  function indexShell() {
    if (body().querySelector('[data-assignment-list]')) return;
    body().innerHTML = `<div data-assignment-needs></div><div class="filter-tabs assignment-tabs" role="group" aria-label="Assignments by status" data-assignment-tabs></div>
      <div class="assignment-filters"><label class="field">Search<input type="search" data-page-search data-filter="query" placeholder="Find an assignment"></label>
      <label class="field">Work<select data-filter="stage"><option value="">All work</option><option value="preparation">Preparation</option><option value="collection">Collection</option><option value="grading">Grading</option><option value="delivery">Delivery</option></select></label>
      <label class="field">Course<select data-filter="course"><option value="">All courses</option>${indexValue.definitions.map(d => `<option value="${e(d.id)}">${e(d.code)}</option>`).join('')}</select></label></div>
      <nav class="assignment-also" aria-label="Every record">${href('#exams?view=list', 'All exam records')}${href('#documents?tab=batches', 'All print jobs')}${href('#teaching-review', 'Teaching review')}</nav>
      <div data-assignment-drafts></div><div data-assignment-list></div>`;
    body().querySelectorAll('[data-filter]').forEach(node => {
      node.value = filters[node.dataset.filter];
      node.addEventListener(node.tagName === 'INPUT' ? 'input' : 'change', () => { filters[node.dataset.filter] = node.value; drawIndex(); });
    });
    // The tabs are patched in place on every draw (focus stays on the one pressed); their clicks are delegated to the group.
    body().querySelector('[data-assignment-tabs]').addEventListener('click', event => {
      const tab = event.target.closest('[data-assignment-tab]')?.dataset.assignmentTab;
      if (tab && tab !== filters.tab) { filters.tab = tab; drawIndex(); }
    });
  }
  // THE OVERVIEW (critique 2026-10-05, the instructor chose: Needs you full width, Running now only while something runs, Recent
  // activity folded, Recorded merged into Delivery). Readiness rows say the same as the tabs' marks: Grading is done once the
  // Grade tab is, and each row's state is also said in words for a screen reader.
  let deliveryRead = null;  // the overview read whose delivery receipts are being read
  /** The overview read's delivery receipts (Return's own read, the same `TeachingReview.delivery` the detail embeds), read after the
   * page is drawn; the summary is drawn again with them while that read is still the one on screen. A failed read says so, as the
   * detail's own `unavailable` did. @param {any} read */
  async function loadDelivery(read) {
    if (deliveryRead === read) return;
    deliveryRead = read;
    let receipts = null;
    try { receipts = await api(`/api/teaching-review/${enc(read.plan_id)}/delivery`); } catch { /* said below */ }
    if (deliveryRead === read) deliveryRead = null;
    if (value !== read || 'delivery' in read) return;  // a newer read reads its own
    read.delivery = receipts || {unavailable: true};
    if (!receipts) read.unavailable = [...(read.unavailable || []), 'delivery'];
    const area = new URLSearchParams(location.hash.split('?')[1]).get('area') || '';
    if (read === context && !root().hidden && ['', 'overview'].includes(area) && /^#assignments\//.test(location.hash)) drawSummary();
  }
  function sharedStamps() {
    if (!('shared_stamps' in value)) return '';
    const lines = sharedStampLines(value.shared_stamps);
    return section('Shared stamps', `${lines.length ? `<ul class="assignment-actions">${lines.map(line => `<li><span>${e(line.label)}</span>${href(`#inbox/job/${enc(line.scan)}/students`, 'Open the scan')}</li>`).join('')}</ul>`
      : '<p class="assignment-quiet">No stamp has come back on two papers.</p>'}<p class="assignment-quiet">${e(SHARED_STAMPS_NOTE)}</p>`, ' data-shared-stamps');
  }

  function drawSummary() {
    const w = value.workflow || {}, life = value.lifecycle;
    const progress = [];
    if (w.pending) progress.push(`${w.pending} grading tasks queued or running`);
    const scanStatus = value.scans.flatMap(s => s.overview.steps.filter(step => step.kind === 'wait').map(step => step.text));
    const queued = [w.new_responses ? `${w.new_responses} responses ready for grading` : '',
      w.awaiting_review ? `${w.awaiting_review} ${w.awaiting_review === 1 ? 'scan waits' : 'scans wait'} for scan checks` : ''].filter(Boolean);
    const tabs = stageMarks(value);
    // Grade is done once the Grade tab is, unless the row itself says a scan still waits for its scan checks (2026-10-11). The rows
    // are named as the tabs are (2026-10-18: Paper, Print, Collect, Grade, Certify, Return).
    const names = {paper: 'Paper', print: 'Print', collected: 'Collect', grading: 'Grade'};
    const checks = life.stages.filter(s => s.key in names)
      .map(s => ({...s, label: names[s.key], ...(s.key === 'grading' && tabs.grade?.state === 'done' && s.state === 'started' && !s.counts?.pending_scans ? {state: 'done'} : {})}));
    // ONE NAVIGATION: plain words for where the grades went (the gradebook column and Gradescope); the receipts are on Return.
    if (value.plan_id && !('delivery' in value)) void loadDelivery(value);
    const receiptText = value.plan_id ? deliveryText(value.delivery) || ('delivery' in value ? 'Not read yet' : 'Reading the receipts…') : 'No grading yet';
    const certified = w.unavailable ? 'failed' : ({current: 'done', changed: 'failed', none: 'idle'})[w.certification?.state || 'none'] || 'started';
    const said = {done: 'Done', started: 'Under way', failed: 'Failed', idle: 'Not started', info: 'Status'};
    const row = (state, label, detail) => `<div data-state="${e(state || 'idle')}"><dt><span class="sr-only">${e(said[state] || said.idle)}: </span>${e(label)}</dt><dd>${e(detail)}</dd></div>`;
    const readiness = `<dl class="assignment-readiness">${checks.map(s => row(s.state, s.label, s.detail)).join('')}
      ${row(certified, 'Certify', w.unavailable ? 'Could not be read' : stateLabel(w.certification?.state || 'none'))}
      ${row('info', 'Return', receiptText)}</dl>`;
    // What runs by itself (2026-10-18, item 9): one muted line from the Compute settings, opening them (I5).
    const byItself = value.plan_id ? automationLine(automation) : null;
    const automatic = byItself ? `<p class="assignment-automatic" data-assignment-automatic>${e(byItself)} ${href(`#grading/${encodeURIComponent(value.plan_id)}?tab=jobs&settings=1`, 'Compute settings')}</p>` : '';
    const unavailable = value.unavailable?.length ? `<p role="status">Some checks could not be read. Refresh to check them again.</p>` : '';
    const tasks = value.needs_you.length ? list(value.needs_you) : '<p class="assignment-quiet">Nothing waits for you here.</p>';
    const prepare = ask && value.needs_you.some(item => ['preparation', 'answer-areas'].includes(item.key))
      ? '<p><button type="button" class="button" data-prepare-assignment>Prepare remaining setup</button></p>' : '';
    const next = life.next && !value.needs_you.length ? `<p>${href(life.next.href, life.next.label || life.next.title)}</p>` : '';
    // The exam record and the gradebook at this assignment (ONE NAVIGATION: the grading page's own bar linked them before).
    const links = [value.event_id ? href(`#exams/${enc(value.event_id)}?tab=sittings`, 'Sittings, students and makeups') : '',
      value.event_id ? href(`#exams/${enc(value.event_id)}?tab=grading`, 'Manual scores') : '',
      `<a class="link-arrow" data-assignment-gradebook href="${e(`#gradebook/${enc(value.term_id)}?assignment=${enc(value.assignment_id)}`)}">Gradebook</a>`].filter(Boolean).join('');
    const runs = activityRuns(value.activity);
    const activity = runs.length ? disclosureMarkup({label: 'Recent activity', hint: String(runs.length), className: 'assignment-activity',
      body: `<ul class="assignment-records">${runs.map(r => `<li>${e(r.label)}${r.count > 1 ? ` <span class="assignment-times">×${r.count}</span>` : ''}<small><time datetime="${e(r.created_at)}">${e(when.format(new Date(r.created_at)))}</time></small></li>`).join('')}</ul>`}) : '';
    const running = progress.length ? section('Queued or running', `<ul class="assignment-records">${[...new Set(progress)].map(p => `<li>${e(p)}</li>`).join('')}</ul>`) : '';
    // WHY GRADING WAITS: the reason beside the count; one that is the instructor's reads in ink, with the Grade step's link.
    const wait = gradingWait(value);
    const why = wait ? `<p class="assignment-wait"${wait.yours ? ' data-yours' : ''}>${e(wait.text)}${wait.href ? ` ${href(wait.href, 'Open Grade')}` : ''}</p>` : '';
    const waitingWork = (queued.length ? `<div class="assignment-waiting"><strong>Waiting to proceed</strong><ul>${[...new Set(queued)].map(p => `<li>${e(p)}</li>`).join('')}</ul>${why}</div>` : '')
      + (scanStatus.length ? `<div class="assignment-waiting"><strong>Scan status</strong><ul>${[...new Set(scanStatus)].map(p => `<li>${e(p)}</li>`).join('')}</ul></div>` : '');
    patchChildren(body(), `<div class="assignment-buttons"><button type="button" class="text-button" data-assignment-refresh>Refresh progress</button></div>${unavailable}<div class="assignment-overview">${sharedStamps()}${section('Needs you', tasks + prepare + next + waitingWork, value.needs_you.length ? ' data-waits="you"' : '')}${running}${section('Readiness', `${readiness}${automatic}<p class="assignment-links">${links}</p>`)}${activity}</div>`);
    body().querySelector('[data-assignment-refresh]').onclick = () => render('assignments', true);
    const prepareButton = body().querySelector('[data-prepare-assignment]');
    if (prepareButton) prepareButton.onclick = async event => {
      const button = event.currentTarget; setBusy(button, true);
      try { await ask(`Review and prepare the remaining grading setup for assignment ${context.assignment_id} in term ${context.term_id}. Inspect the current owner records. Use the focused preparation tools and leave changes for my approval; never send, certify or publish.`, {reviewOnly: true}); }
      catch (cause) { toast(cause.message, true); } finally { setBusy(button, false); }
    };
  }
  // ASSIGNMENT-WORKSPACE: collection exceptions and exact participant corrections stay beside retained scan links.
  async function drawCollection() {
    if (body().querySelector('[data-collection-dirty]')) return;
    const c = value.workflow?.collection || null, stage = (value.lifecycle?.stages || []).find(s => s.key === 'collected');
    // NOTHING SCANNED YET (2026-10-11): until a paper is back nobody is "without a scanned paper": the lead says how many are
    // expected, as the Readiness row does, and Upload scans is the page's action.
    // ONE COUNT OF MISSING PAPERS (2026-10-18): the lead's "without a scanned paper" is `missingPapers`, a plain count (THE ZEROS ARE
    // SAID AT CERTIFY, 2026-11-05); incomplete papers are said apart.
    const {back, without, incomplete} = missingPapers(c);
    const missing = without + incomplete;
    const record = value.event_id ? `#exams/${enc(value.event_id)}?tab=collection` : '';
    const total = back ? (c.complete || 0) + missing + (c.absent || 0) + (c.excused || 0) + (c.needs_review || 0) : 0;
    const lead = back ? `<p class="assignment-lead">${e(`${c.complete} of ${total} students have a complete paper`)}${missing ? '' : '.'}${without ? e(` · ${without} without a scanned paper`) : ''}${incomplete ? e(` · ${incomplete} incomplete`) : ''}${c.absent || c.excused ? e(` · ${c.absent || 0} absent · ${c.excused || 0} excused`) : ''}</p>` : stage ? `<p class="assignment-lead">${e(stage.detail)}</p>` : '';
    const links = value.scans.map(scan => ({key: scan.job_id, href: `#inbox/job/${enc(scan.job_id)}`, label: scan.overview.headline || 'Open scan', count: scan.pages, unit: 'pages'}));
    // I2: this assignment's scans still in their scan checks, each opening its scan page inside the assignment (from the read on
    // screen; nothing more is read). `?scans=waiting` (every "Finish the scan checks") brings the list into view.
    const waiting = waitingScans(value), asked = new URLSearchParams(location.hash.split('?')[1]).get('scans') === 'waiting';
    const checks = waiting.length ? `<div class="assignment-scan-checks" data-assignment-waiting-scans tabindex="-1"><h3 class="assignment-subhead">Waiting for scan checks · ${waiting.length}</h3><ul class="assignment-actions">${waiting.map(s =>
      `<li data-patch-key="${e(`waiting-${s.key}`)}"><span>${e(s.label)}${s.detail && s.detail !== s.label ? ` <small>${e(s.detail)}</small>` : ''}</span>${href(s.href, 'Finish the scan checks')}</li>`).join('')}</ul></div>`
      : asked ? '<p class="assignment-quiet" data-assignment-waiting-scans tabindex="-1">No scan of this assignment waits for its scan checks.</p>' : '';
    // COLLECT'S ACTION IS IN THE ROW (2026-10-11): the students without a paper are decided in their own rows below, so no button
    // repeats them and the exam record's collection is linked whether or not a paper is missing.
    const more = [record ? href(record, 'Student collection record', 'button') : '', `<button type="button" class="button${missing || links.length ? '' : ' primary'}" data-action="inbox-upload-scans">Upload scans</button>`,
      value.plan_id ? href(`#grading/${enc(value.plan_id)}?tab=papers`, 'Scan records', 'button') : '', value.plan_id ? href(`#grading/${enc(value.plan_id)}?tab=bubble`, 'Bubble sheets', 'button') : ''].join('');
    // The student list is the collection review's own (`data-patch-static`): this redraw leaves it, and what is open in it, where it
    // is, and the review patches its rows in place (the 30 s refresh emptied and rebuilt it before, folding every row).
    patchChildren(body(), sharedStamps() + section('Collected papers', `${lead}<div class="assignment-buttons">${more}</div>${checks}<div data-collection-exceptions data-patch-static="${e(value.event_id || '')}"></div>${links.length ? `<details class="disclosure"><summary>All scans</summary>${list(links)}</details>` : ''}`));
    const shown = location.hash;
    // Brought into view once per arrival at that address; the quiet refresh never moves the page.
    if (asked && waitingShownFor !== shown) {
      const node = body().querySelector('[data-assignment-waiting-scans]');
      if (node) { waitingShownFor = shown; node.scrollIntoView({block: 'start'}); node.focus({preventScroll: true}); }
    }
    if (!asked) waitingShownFor = '';
    // I1: the Upload scans asked for by the address opens once, over Collect.
    if (pendingUpload) { const target = pendingUpload; pendingUpload = ''; if (target === shown) body().querySelector('[data-action="inbox-upload-scans"]')?.click(); }
    if (value.event_id) {
      await collectionReview.show(body().querySelector('[data-collection-exceptions]'), value.event_id,
        () => location.hash === shown && areaFor(location.hash) === 'collect', () => render('assignments', true));
    }
  }
  async function canonical(area, active) {
    if (area === 'create') {
      await documents().renderWorkspace(body(), context, active, () => render('assignments', true));
      // A MAKEUP VERSION WAITING TO BE APPROVED: the window asked for by the address opens once, over Create.
      if (pendingMakeup) { const target = pendingMakeup; pendingMakeup = ''; if (active() && target === location.hash) body().querySelector('[data-workspace-makeup]')?.click(); }
      return;
    }
    if (area === 'collect') { await drawCollection(); return; }
    if (area && area !== 'overview') {
      if (context.plan_id) { location.hash = areaHref(context, area); return; }
      // Before grading is prepared (2026-10-18, item 12): Prepare offers Prepare grading; Grade, Certify and Return each say in one
      // line what they wait for, with the way there, instead of repeating Prepare's panel.
      const byHand = context.event_id ? href(`#exams/${enc(context.event_id)}?tab=grading`, 'Enter scores by hand') : '';
      if (area !== 'prepare') {
        const way = area === 'grade' ? [href(assignmentHref(context, 'prepare'), 'Open Prepare'), byHand]
          : area === 'return' ? [href(`#gradebook/${enc(context.term_id)}?assignment=${enc(context.assignment_id)}`, 'Gradebook')] : [];
        patchChildren(body(), `<p class="assignment-quiet" data-assignment-waits="${e(area)}">${e(WAITS_FOR_PLAN[area])}</p>${way.filter(Boolean).length ? `<p class="assignment-links">${way.filter(Boolean).join('')}</p>` : ''}`);
        return;
      }
      // PREPARE STARTS WITH ITS PREREQUISITE: without its paper, the paper comes first (creating it in Create also prepares grading).
      const paper = (value?.lifecycle?.stages || []).find(s => s.key === 'paper');
      const noPaper = Boolean(paper && paper.state !== 'done');
      const first = noPaper ? `<p>${href(assignmentHref(context, 'create'), 'Add paper in Create', 'button primary')}</p>` : '';
      patchChildren(body(), section('Prepare this assignment', `${first}<p>Prepare the grading alongside the paper. Printing can proceed while the grading setup is unfinished.</p><button type="button" class="button${noPaper ? '' : ' primary'}" data-start-plan>Prepare grading</button>${byHand ? `<p>${byHand}</p>` : ''}`));
      body().querySelector('[data-start-plan]').onclick = async event => {
        const button = event.currentTarget; setBusy(button, true);
        try {
          const plan = await api('/api/grading/assignments', 'POST', {assignment_id: context.assignment_id, term_id: context.term_id});
          if (active()) location.hash = `#grading/${enc(plan.id)}?tab=rubrics&workspace=1`;
        } catch (error) { toast(error.message, true); } finally { setBusy(button, false); }
      };
      return;
    }
    drawSummary();
  }
  // `quiet`: the 30 s refresh, which keeps the delivery receipts this page already read.
  async function render(view, force = false, quiet = false) {
    // DOCUMENTS / ASSIGNMENT-WORKSPACE: ?create=1 and ?quiz=new (Home, the palette, old #documents links) open their window
    // once. The address drops them first, so Back, a reload or a refresh never opens it again.
    if (view === 'assignments') {
      const [path, query = ''] = location.hash.split('?'), args = new URLSearchParams(query);
      const action = args.get('create') === '1' ? 'create' : args.get('quiz') === 'new' ? 'draft' : '';
      if (path === '#assignments' && action) {
        args.delete('create'); args.delete('quiz');
        const rest = args.toString();
        history.replaceState(history.state, '', `${path}${rest ? `?${rest}` : ''}`);
        pendingAction = action; force = true;
      }
      // I1: every "Upload scans" of an assignment opens Collect's own Upload scans in place, once (Back or a reload does not).
      if (path.startsWith('#assignments/') && args.get('area') === 'collect' && args.get('upload') === '1') {
        args.delete('upload');
        history.replaceState(history.state, '', `${path}?${args.toString()}`);
        pendingUpload = `${path}?${args.toString()}`; force = true;
      }
      // A MAKEUP VERSION WAITING TO BE APPROVED: its Needs you row opens the draft window the same way.
      if (path.startsWith('#assignments/') && args.get('area') === 'create' && args.get('makeup') === '1') {
        args.delete('makeup');
        history.replaceState(history.state, '', `${path}?${args.toString()}`);
        pendingMakeup = `${path}?${args.toString()}`; force = true;
      }
    }
    const current = location.hash;
    // The app's view can lag the address by one event (a hash set during a render, before its hashchange arrives): that
    // call once read #grading/<plan> as an assignment's address. The hashchange's own render follows, so this one waits.
    // Two working views lag the same way (an Inbox scan's handoff to its grading).
    const addressed = routePath(current).split('/')[0];
    if ((view === 'assignments') !== /^#assignments(?:[/?]|$)/.test(current) || (workingViews.has(addressed) && addressed !== view)) return;
    if (!force && current === route) { if (context) paintHeader(); return; }
    if (!force && context && samePlace(current, route)) { route = current; nav(); paintHeader(); return; }
    const token = ++generation, changed = current !== route;
    if (changed) waitingShownFor = '';
    if (pendingUpload && pendingUpload !== current) pendingUpload = '';
    if (pendingMakeup && pendingMakeup !== current) pendingMakeup = '';
    if (pendingAction && routePath(current) !== 'assignments') pendingAction = '';
    clearTimeout(timer);
    route = current;
    if (view !== 'assignments' && !workingViews.has(view)) {
      context = null; value = null; ownerSpec = null;
      release(); restoreOwner(); root().hidden = true; cleared(); return;
    }
    shell();
    const active = () => token === generation && location.hash === current;
    if (changed) {
      // A move between pages of an assignment (a grading tab, a scan's next stage) keeps the shell, its navigation and
      // the owner's editor where they are while the new address resolves: nothing moves out and back, so the page does
      // not jump and focus stays. The navigation is held until the read answers; an empty or failed read clears it.
      const previous = context || value || (pending ? held : null);
      const continuing = Boolean(previous) && !root().hidden && (view === 'assignments' || mayResolve(current));
      context = null; value = null; ownerSpec = null;
      body().innerHTML = '';
      if (continuing) {
        pending = true; held = previous; provisional = true;
        navNode().setAttribute('aria-busy', 'true');
        if (workingViews.has(view)) mountOwner(view); else restoreOwner();
        heldHeader(previous);
      } else {
        release(); restoreOwner(); root().hidden = view !== 'assignments';
      }
    }
    try {
      if (view === 'assignments' && !current.split('?')[0].slice(1).includes('/')) {
        if (changed) {
          // Links into the list may name its filters and tab (a past term's course page: ?course=<course>&status=archived).
          const args = new URLSearchParams(current.split('?')[1]), tab = tabFromStatus(args.get('status'));
          filters.stage = args.get('filter') || '';
          if (args.has('course')) filters.course = args.get('course') || '';
          if (tab) filters.tab = tab;
        }
        context = null; release(); restoreOwner(); root().hidden = false; cleared();
        // Both open for the course the list is filtered to, as the former page's header did.
        header.set({route: current, title: 'Assignments', subtitle: 'From the first draft to returned grades.',
          action: {label: 'Create assignment', icon: 'plus', run: startAssignment},
          // EXAMS: an exam record (sittings, students, makeups) is still created by its owner's dialog (app.js "add-exam").
          secondary: []});
        // Coming back to the list draws what it last read while the new read (about 2 s on the live term) is on its way, as the class
        // schedule does (2026-11-01, "the assignment's page takes a long time to load"); the read patches it in place.
        if (changed && indexValue) { indexShell(); drawIndex(); }
        else if (changed || !indexValue) body().innerHTML = loadingMarkup('Loading assignments');
        const result = await api('/api/assignments');
        if (!active()) return;
        indexValue = result; indexShell(); drawIndex();
        if (pendingAction) { const action = pendingAction; pendingAction = ''; await documents()[action](filters.course); }
      } else if (view === 'assignments') {
        const parts = current.split('?')[0].split('/');
        if (changed) body().innerHTML = loadingMarkup('Loading assignment');
        // PERFORMANCE: delivery receipts change only through the instructor's own certify, send and publish actions on other pages.
        // The quiet refresh keeps those read on opening (or on Refresh progress / Try again) and asks for the rest only. The page never
        // waits for them (2026-11-01, "the assignment's page takes a long time to load": they were 1.5 s of Exam 2's 2.1 s): it draws
        // from `?delivery=0` and the overview's Return line fills in once `loadDelivery` has read them; no other area shows them.
        const url = `/api/assignments/${enc(decodeURIComponent(parts[1]))}/${enc(decodeURIComponent(parts[2]))}`;
        const kept = quiet && !changed && value ? value : null;
        // The Compute settings, for Readiness's "Runs by itself" line: read with the overview when it opens or is refreshed by hand,
        // never by the quiet refresh (no new periodic read); a failed read leaves the line out.
        const overview = !new URLSearchParams(current.split('?')[1]).get('area');
        const settings = overview && !kept ? api('/api/grading/settings').then(data => data?.settings || null, () => null) : null;
        const result = await api(`${url}?delivery=0`);
        if (settings) automation = await settings;
        if (!active()) return;
        if (kept && 'delivery' in kept && result.plan_id === kept.plan_id) {  // A plan made since reads its receipts once now.
          result.delivery = kept.delivery;
          if (kept.unavailable?.includes('delivery')) result.unavailable = [...result.unavailable, 'delivery'];
        }
        release(false);
        value = context = result; root().hidden = false;
        marks = stageMarks(result); marksFor = pairKey(result); marksAt = Date.now();
        nav(); paintHeader();
        const area = new URLSearchParams(current.split('?')[1]).get('area') || '';
        await canonical(AREAS.some(([key]) => key === area) ? area : '', active);
      } else {
        const result = await api(`/api/assignments/resolve?route=${enc(current)}`);
        if (!active()) return;
        context = result.context;
        if (context) { release(false); root().hidden = false; nav(); mountOwner(view); paintHeader(); void loadMarks(context); watchMarks(); }
        else if (result.draft) {
          const wasProvisional = release();
          cleared(); root().hidden = false; mountOwner(view);
          navNode().innerHTML = href('#assignments', 'All assignments') + '<span aria-current="page">Create · draft</span>';
          ownerHeader(wasProvisional);
        } else {
          const wasProvisional = release();
          restoreOwner(); root().hidden = true; cleared();
          ownerHeader(wasProvisional);
        }
      }
      if (active()) root().querySelector('[data-assignment-error]').innerHTML = '';
    } catch (error) {
      if (!active()) return;
      if (pending) { ownerHeader(release()); cleared(); }
      root().hidden = false;
      root().querySelector('[data-assignment-error]').innerHTML = calloutMarkup({tone: 'danger', title: 'Assignment could not be refreshed', body: e(error.message)}) + '<button type="button" class="text-button" data-retry-assignment>Try again</button>';
      root().querySelector('[data-retry-assignment]').onclick = () => render(view, true);
      if (!value && !indexValue) body().innerHTML = '';
    } finally { if (active()) schedule(view); }
  }
  return {render, editorHeader, context: () => context};
}
