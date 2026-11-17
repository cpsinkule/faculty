import {mountRegions} from "./grading-regions.js";
import {helpers} from "./grading-ui.js";
import {inboxNoticeMarkup, versionNoticeMarkup, gradebookLink, assignmentNext} from "./grading-overview.js";
import {rubricView} from "./grading-rubrics.js";
import {jobsView} from "./grading-jobs.js";
import {reviewView} from "./grading-review.js";
import {certificationView} from "./grading-certification.js";
import {createSettingsDialog} from "./grading-settings-dialog.js";
import {workflowMarkup, workflowTab, guideSteps, uploadScansHref, waitingScansHref, SCAN_CHECKS_LABEL} from "./grading-guide.js";
import {loadingMarkup, leaveMessage} from "./ui.js";
import {bubbleView} from "./grading-bubble-key.js";
import {mountLocalWork} from "./grading-background.js";
import {patchChildren} from "./dom-patch.js";

// Stage names for the trail's last crumb; the six workflow steps name themselves (guideSteps), these two are utilities.
const UTILITY = {papers: "Scan records", bubble: "Bubble sheets"};
const n = value => Number(value || 0).toLocaleString();
const plural = (count, one, many = `${one}s`) => `${n(count)} ${count === 1 ? one : many}`;
const termName = plan => `${plan.term === "Other" ? plan.term_other : plan.term} ${plan.year}`;
// Typing somewhere, or a dialog, or unsaved work: an automatic refresh would take it away.
const typing = node => Boolean(node && (node.isContentEditable || node.tagName === "TEXTAREA" || node.tagName === "SELECT"
  || (node.tagName === "INPUT" && !["checkbox", "radio", "button", "submit", "reset"].includes(node.type))));

// The Grading index is retired (2026-10-18, the pipeline review's P3, approved): Assignments lists every assignment, and compute
// availability and costs are a section of Compute settings. Any `#grading` address without an assignment (`?view=list`, an old
// bookmark) opens Assignments; `#grading?settings=1` opens Compute settings over it.
export const RETIRED_INDEX = "#assignments";

// How long an assignment's plan read serves its other steps (perf-1; the instructor's freshness call, 10 s): moving between steps
// and review links of one assignment does not read the whole assignment again, unless something was written since.
const PLAN_REUSE_MS = 10000;
// The Review step changes what the step graph counts with every decision (a review saved, a gallery approval, a regrade or retry it
// sends, a flag review that finished) and reads only its own queue afterwards (PERFORMANCE "REVIEW DETAILS"), so the graph read
// "Review 0% · 0/5" beside a rail at 4 of 5 final until a reload (2026-10-11). The step says when (`changed()`); the plan is then
// read again behind it and handed to `draw`, which patches the graph alone. The plan read holds the lifecycle lock, so it waits
// PLAN_FOLLOW_MS after the LAST change (a run of quick approvals is followed by one read once it pauses, never by one each) and
// runs one at a time, after a read under way and never beside it. A read that another change overtook is not drawn: the plan is
// read once more. A failed read leaves the graph as it is; the next change reads again. Nothing is read once the page is left.
const PLAN_FOLLOW_MS = 1000;
/**
 * @param {() => Promise<any>} read @param {(plan: any) => void} draw @param {() => boolean} active
 * @param {{wait?: number, timers?: {setTimeout: (run: () => void, ms: number) => any, clearTimeout: (timer: any) => void}}} [options]
 */
export function planFollower(read, draw, active, {wait = PLAN_FOLLOW_MS, timers = globalThis} = {}) {
  let timer = null, reading = false, overtaken = false;
  const run = async () => {
    if (!active()) return;
    if (reading) { timer = timers.setTimeout(run, wait); return; }
    reading = true; overtaken = false;
    try {
      const plan = await read();
      if (active() && !overtaken) draw(plan);
    } catch {
      // The graph keeps what it shows.
    } finally { reading = false; }
  };
  return {
    changed() {
      if (!active()) return;
      overtaken = true;
      timers.clearTimeout(timer);
      timer = timers.setTimeout(run, wait);
    },
    stop() { timers.clearTimeout(timer); },
  };
}

export function createGrading(options) {
  // Every write made through this view (its steps, dialogs and settings) forgets the reused plan read, both when it starts and when
  // it ends, so no step draws a plan read before a write finished; `writes` lets a read that raced a write go unkept.
  let lastPlan = null, writes = 0;
  const forget = () => { writes++; lastPlan = null; };
  const api = (path, method = "GET", ...rest) => {
    if (String(method).toUpperCase() === "GET") return options.api(path, method, ...rest);
    forget();
    const result = options.api(path, method, ...rest);
    Promise.resolve(result).then(forget, forget);
    return result;
  };
  const h = helpers({...options, api});
  const computeSettings = createSettingsDialog(h, options.icon);
  const root = () => document.querySelector("#grading-section");
  let generation = 0, route = "", ready = false, loading = false, pendingOpen = null, pendingSettings = false;
  let guideResize = null, heightHold = null, planFollow = null;
  // The Grade or Review step on screen ({aid, route, signature}) and the last grading signature of the shell's overview (below).
  let drawnStep = null, gradingSeen = null;
  window.addEventListener("hashchange", () => {
    if (!location.hash.startsWith("#grading")) {
      generation++; ready = false; loading = false; route = "";
      pendingSettings = false;
      planFollow?.stop();
      guideResize?.disconnect();
      heightHold?.();
      lastPlan = null; drawnStep = null;
    }
  });
  // The shell's workspace overview (read about every 30 s wherever the instructor is) says when grading work starts or stops
  // anywhere, and when an assignment's review or failed card appears or clears. Any such change forgets the reused plan read; on
  // the Grade step, or Review's question tiles, of that assignment the step is drawn again once per change (a batch that started
  // by itself or from another tab shows up), never while the instructor is in a dialog, typing or holding unsaved work: a busy
  // moment is retried at the next overview read. The redraw is quiet: the folds the instructor opened, the control with keyboard
  // focus and the scroll stay as they were. Ongoing progress stays with the batch cards' own poll; nothing is read here.
  const gradingSignature = (data, aid = "") => {
    const queue = (data?.operations?.queues || []).find(q => q.key === "frq_grading");
    const keys = (data?.attention || []).map(card => String(card.key || "")).filter(key => key.startsWith(aid ? `grading:${aid}:` : "grading:")).sort();
    return [((queue?.pending || 0) + (queue?.running || 0)) > 0, ...keys].join("|");
  };
  options.overviewStore?.subscribe(data => {
    if (!data) return;
    const all = gradingSignature(data);
    if (gradingSeen !== null && all !== gradingSeen) lastPlan = null;
    gradingSeen = all;
    if (!drawnStep || drawnStep.route !== location.hash || route !== location.hash) return;
    const mine = gradingSignature(data, drawnStep.aid);
    if (drawnStep.signature === null) { drawnStep.signature = mine; return; }
    if (mine === drawnStep.signature || !ready || loading || root()?.hidden || busy()) return;
    drawnStep.signature = mine;
    void render(true, {quiet: true});
  });
  // A background refresh waits while the instructor is in a dialog, typing, or holding unsaved work.
  const busy = () => Boolean(document.querySelector("dialog[open]") || leaveMessage() || root()?.querySelector('[data-unsaved="true"]')
    || (root()?.contains(document.activeElement) && typing(document.activeElement)));
  const openSettings = trigger => { void computeSettings.open(trigger || root()?.querySelector('[data-grading-action="settings"]')); };
  // `quiet`: a redraw nobody asked for keeps the page as the instructor left it.
  async function render(force = false, {quiet = false} = {}) {
    if (!location.hash.startsWith("#grading") || !root()) return;
    // The shell renders both on navigation and after its state request finishes.
    // Reuse the current load instead of starting another set of locked API reads.
    if (!force && (ready || loading) && route === location.hash) return;
    planFollow?.stop();
    guideResize?.disconnect();
    heightHold?.();
    // A forced render of the address on screen (Refresh, the read after a save, finished background work) redraws the same step.
    // It always reads, and whatever it superseded is forgotten: no later step reuses a plan read from before it.
    if (force) lastPlan = null;
    const previous = route, arrived = location.hash;
    route = location.hash; ready = false; loading = true;
    const current = ++generation;
    const active = () => current === generation && route === location.hash && !root().hidden;
    const match = route.match(/^#grading(?:\/([^?]+))?(?:\?(.*))?$/);
    const params = new URLSearchParams(match?.[2] || "");
    const aid = match?.[1] || "";
    let focus = params.get("focus") || "";
    let requestedTab = params.get("tab") || "overview";
    const rewrite = () => {
      route = `#grading${aid ? `/${aid}` : ""}${params.size ? `?${params}` : ""}`;
      history.replaceState(history.state, "", route);
    };
    // Older settings bookmarks, and Workspace settings' link (#grading?settings=1), open the popup over the grading workspace.
    if (requestedTab === "settings" || params.get("settings") === "1") {
      pendingSettings = true;
      if (requestedTab === "settings") requestedTab = "overview";
      params.delete("tab");
      params.delete("settings");
      if (requestedTab !== "overview") params.set("tab", requestedTab);
      rewrite();
    }
    if (['approve', 'rubric-quality', 'templates'].includes(requestedTab)) {
      // Older links: the rubric check now shares the answer key's step, and the templates share the rubrics' step.
      if (requestedTab === 'rubric-quality') params.set('open', 'quality');
      if (requestedTab === 'templates') focus = 'templates';
      requestedTab = requestedTab === 'rubric-quality' ? 'answer-key' : 'rubrics';
      params.set('tab', requestedTab);
      rewrite();
    }
    // Retain older dialog links, while showing the task they actually open.
    const legacyStep = {quality: 'answer-key', 'answer-key': 'answer-key', templates: 'rubrics'}[params.get('open')];
    if (legacyStep) {
      requestedTab = legacyStep;
      params.set('tab', legacyStep);
    }
    // "open" asks for a dialog once. Drop it from the address so a refresh after saving does not ask again, and
    // remember it until a render really mounts the page: a newer render can supersede this one while it loads.
    if (params.get("open")) {
      pendingOpen = {aid, action: params.get("open")};
      params.delete("open");
      rewrite();
    }
    // The retired Grading index (P3/I7): no assignment in the address opens Assignments in its place (no history entry is added),
    // and a settings request opens Compute settings over it once Assignments is shown (the dialog closes on any address change).
    if (!aid) {
      const settings = pendingSettings;
      pendingSettings = false; loading = false; ready = true;
      if (settings) window.addEventListener("hashchange", () => { setTimeout(() => openSettings(), 0); }, {once: true});
      location.replace(RETIRED_INDEX);
      return;
    }
    // Moving between one assignment's steps keeps its heading and graph on screen: only the step's own content waits.
    const bar = root().querySelector(":scope > .grading-heading"), content = root().querySelector(":scope > .grading-content");
    const sameFrame = Boolean(bar && content && bar.dataset.gradingAid === aid);
    // Redrawing the step on screen keeps it: no skeleton, the drawn step stays until the new one replaces it, the page keeps its
    // height while it is rebuilt and the scroll stays where the instructor was (a step that scrolls on purpose says so).
    const sameStep = sameFrame && force && previous === location.hash;
    const kept = sameStep ? content : null, keepY = sameStep ? window.scrollY : null;
    const reusable = !force && lastPlan?.aid === aid && Date.now() - lastPlan.at < PLAN_REUSE_MS;
    if (sameStep) {
      content.setAttribute("aria-busy", "true");
      heightHold = holdHeight();
    } else if (sameFrame && !reusable) {
      content.setAttribute("aria-busy", "true");
      content.innerHTML = loadingMarkup("Loading this step…", 4);
    } else if (!sameFrame) root().innerHTML = loadingMarkup("Loading grading workspace…", 4);
    try {
      // A step needs this assignment's activity, not a second locked walk of every assignment, in one server read. A step reuses
      // this assignment's read for 10 s when nothing was written since; Refresh and every write read again.
      let plan = null;
      if (reusable) plan = structuredClone(lastPlan.data);
      else {
        const before = writes;
        plan = await h.api(`/api/grading/assignments/${aid}?include=activity`);
        if (writes === before && current === generation) lastPlan = {aid, data: structuredClone(plan), at: Date.now()};
      }
      const overview = plan.activity;
      if (!active()) return;
      const planJobs = overview.jobs.filter(j => j.assignment_id === plan.id);
      const actions = `<div class="grading-actions"><button type="button" class="icon-button" data-grading-action="refresh" aria-label="Refresh" title="Refresh">${options.icon("refresh")}</button><button type="button" class="button grading-settings-trigger" data-grading-action="settings" aria-haspopup="dialog">${options.icon("settings")}<span>Compute settings</span></button></div>`;
      // PREPARE STARTS WITH ITS PREREQUISITE, extended (2026-10-18, pipeline review item 12): a plan whose scans have not cleared their
      // scan checks yet (`entry.ready` false) opens its steps like a plan set up before papers. Rubrics, templates and answer areas
      // come from the paper and its blank templates, never from scans; only grading waits for papers, and the Grade step says so.
      // No server gate changes: the plan read is the same, and every step's own write keeps its rules.
      const tab = workflowTab(requestedTab, plan, planJobs);
      const stage = UTILITY[tab] || guideSteps(plan, planJobs).find(step => step.tab === tab)?.label || "";
      options.header?.set({route: location.hash, title: plan.title, context: `${plan.code} · ${termName(plan)}`, subtitle: "", action: null,
        crumbs: [{label: "Home", href: "#overview"}, {label: "Assignments", href: RETIRED_INDEX}, {label: plan.title, href: `#grading/${plan.id}`}, ...(stage ? [{label: stage}] : [])],
        tab: stage ? `${plan.title} · ${stage}` : plan.title});
      const context = `<nav class="grading-context" aria-label="Assignment"><a href="${RETIRED_INDEX}">← All assignments</a><a data-grading-scan-records href="#grading/${h.e(plan.id)}?tab=papers" ${tab === "papers" ? 'aria-current="page"' : ""}>Scan records</a><a data-grading-bubble href="#grading/${h.e(plan.id)}?tab=bubble" ${tab === "bubble" ? 'aria-current="page"' : ""}>Bubble sheets${plan.bubble?.sheets ? ` (${h.e(n(plan.bubble.sheets))})` : ""}</a>${gradebookLink(plan, h)}${gradescopeLink(plan)}${plan.event_id ? `<a data-grading-exam href="#exams/${h.e(encodeURIComponent(plan.event_id))}">Exams page</a>` : ""}</nav>`;
      // The link the instructor followed in the page bar or the graph keeps keyboard focus across the rebuild (a step link, Scan
      // records, Bubble sheets), found again by where it leads; a link the page did not arrive by is left alone.
      const followed = document.activeElement?.matches("a") && [arrived, location.hash].includes(document.activeElement.getAttribute("href"));
      const barLink = sameFrame && (followed || sameStep) && document.activeElement?.matches("a") && document.activeElement.closest(":is(.grading-page-bar, [data-grading-workflow])")
        ? {region: document.activeElement.closest(".grading-page-bar") ? ".grading-page-bar" : "[data-grading-workflow]", href: document.activeElement.getAttribute("href")} : null;
      // Refresh (or Compute settings) pressed from the keyboard keeps focus: the bar is drawn again.
      const barAction = document.activeElement?.closest(".grading-page-bar") ? document.activeElement.dataset.gradingAction || "" : "";
      const quietState = quiet && sameStep ? pageState() : null;
      root().innerHTML = `<header class="grading-heading grading-page-bar" data-grading-aid="${h.e(aid)}">${context}${actions}</header>
        ${entryNotice(plan) || inboxNoticeMarkup(plan, h)}${versionNoticeMarkup(plan, h)}<div data-grading-local-work></div><div data-grading-workflow></div><div class="grading-content"></div>`;
      if (kept) root().querySelector(":scope > .grading-content").replaceWith(kept);
      // The graph is drawn once per render; a step that hands back a newer plan (`onPlan`, `planFollower`) patches it in place
      // (dom-patch.js), so a count that changes moves neither the graph's scroll nor the focus on one of its links, and a plan
      // that draws the same graph writes nothing (FRONTEND "Quiet refresh contract").
      let guideDrawn = null;
      const drawGuide = latest => {
        if (!active()) return;
        const guide = root().querySelector("[data-grading-workflow]"), markup = latest ? workflowMarkup(latest, planJobs, tab, h) : "";
        if (markup === guideDrawn) return;
        const first = guideDrawn === null;
        guideDrawn = markup;
        if (!first) { patchChildren(guide, markup); return; }
        guideResize?.disconnect();
        guide.innerHTML = markup;
        const scroller = guide.querySelector('.grading-pipeline-scroll'), selected = guide.querySelector('[aria-current="step"]');
        if (scroller && selected) {
          const centerStep = () => { if (active() && scroller.isConnected) centre(scroller, selected); };
          centerStep();
          // Keep the current task visible when a desktop window narrows; manual scrolling still works.
          guideResize = new ResizeObserver(centerStep);
          guideResize.observe(scroller);
        }
      };
      drawGuide(plan);
      // The Review step's changes are followed by a read of the plan for the graph alone (`planFollower`, above): the response on
      // screen, its focus and the scroll are not touched.
      planFollow = planFollower(() => h.api(`/api/grading/assignments/${aid}`), drawGuide, active);
      // When the local worker finishes for this assignment the step shows its results by itself, unless that would take away
      // a dialog, typing or unsaved work; then it says so and offers the refresh.
      mountLocalWork(root().querySelector("[data-grading-local-work]"), plan, h, active, {onFinished: (force = false) => {
        if (!force && busy()) return false;
        void render(true);
        return true;
      }});
      bindBar();
      if (barLink) [...root().querySelectorAll(`${barLink.region} a`)].find(link => link.getAttribute("href") === barLink.href)?.focus({preventScroll: true});
      if (barAction) root().querySelector(`.grading-page-bar [data-grading-action="${barAction}"]`)?.focus({preventScroll: true});
      const host = root().querySelector(":scope > .grading-content");
      // A step drawn for the first time waits behind a skeleton (content-only loading) until its own read is drawn.
      if (!kept) { host.setAttribute("aria-busy", "true"); host.innerHTML = loadingMarkup("Loading this step…", 4); }
      const setupTabs = ['rubrics', 'answer-key', 'locations'];
      // `open` asks a step for one control once: a setup step's dialog, Certification and Export's Gradescope fold, or Review's
      // Failed to grade (open=failed).
      const open = (setupTabs.includes(tab) || tab === "export" || tab === "review") && pendingOpen?.aid === aid ? pendingOpen.action : "";
      pendingOpen = null;
      // A step view returns true when it scrolled on purpose (to a question it reopened, a panel it was asked to show); otherwise a
      // redraw of the step on screen puts the scroll back where the instructor was.
      let scrolled = false;
      if (setupTabs.includes(tab)) scrolled = rubricView(host, plan, h, () => render(true), active, {focus, open, stage: tab, locations: tab === 'locations', unified: tab === 'rubrics', onPlan: drawGuide, editQuestion: q => questionDialog(plan, q), busy, ask: options.ask}) === true;
      if (tab === "papers") papersView(host, plan, planJobs);
      if (keepY !== null && !scrolled && active() && Math.abs(window.scrollY - keepY) > 1) window.scrollTo({top: keepY, behavior: "instant"});
      if (tab === "bubble") await bubbleView(host, plan, h, () => render(true), active, {busy});
      // Certification and Review keep their open question/response in the address without navigation:
      // the address and this render's route change together, so the page stays active.
      const address = changes => {
        if (current !== generation || route !== location.hash) return;
        for (const [key, value] of Object.entries(changes)) { if (value) params.set(key, value); else params.delete(key); }
        rewrite();
      };
      if (tab === "export") await certificationView(host, plan, h, active, {question: params.get("question") || "", jobs: planJobs, refresh: () => render(true), address, onPlan: drawGuide, open, busy});
      if (tab === "jobs") await jobsView(host, plan, overview, h, () => render(true), active, {openSettings});
      if (tab === "review") await reviewView(host, plan, h, () => render(true), active, {job: params.get("job") || "", question: params.get("question") || "", group: params.get("group") || "",
        browse: params.get("browse") === "all", response: params.get("response") || "", status: params.get("status") || "", spot: params.get("spot") || ""}, address, {open, onChanged: planFollow?.changed});
      if (active()) {
        ready = true;
        host.removeAttribute("aria-busy");
        if (quietState) restorePage(quietState, {scroll: !scrolled});
        heightHold?.(true);
        // The Grade step, Review's empty state and its question pages are drawn again when grading starts or stops (the overview
        // subscription above); a review desk on screen never is (the workspace keeps group and response in the address).
        const watched = tab === "jobs" || (tab === "review" && !params.get("response") && !params.get("group") && params.get("browse") !== "all");
        drawnStep = watched ? {aid, route, signature: drawnStep?.route === route ? drawnStep.signature : options.overviewStore?.get() ? gradingSignature(options.overviewStore.get(), aid) : null} : null;
        if (pendingSettings) {
          pendingSettings = false;
          openSettings();
        }
      }
    } catch (error) {
      // A step that failed to draw leaves nothing to reuse: the next step reads its plan again.
      lastPlan = null;
      if (!active()) return;
      // Keep the explicit retry state when the shell finishes refreshing too. With the assignment's heading and graph still on
      // screen, only the step says what failed.
      ready = true;
      heightHold?.();
      const target = sameFrame && root().querySelector(":scope > .grading-content") || root();
      target.removeAttribute("aria-busy");
      target.innerHTML = `<p role="alert">${h.e(error.message)}</p>${h.button("retry", "Try again")}`;
      target.querySelector('[data-grading-action="retry"]').onclick = () => { void render(true); };
    } finally {
      if (current === generation) loading = false;
    }
  }
  // What a quiet redraw of a step keeps (grading-review-7): the folds the instructor opened, the control with keyboard focus and the
  // scroll. A fold or control is found again by its batch, its own attributes and its place among look-alikes; a summary by its
  // fold. Folds are only reopened, never shut: a fold the new draw opens by itself (a batch that failed) stays open.
  function identity(node) {
    if (node.tagName === "SUMMARY" && node.parentElement?.tagName === "DETAILS") return `summary ${identity(node.parentElement)}`;
    const own = [...node.attributes].filter(a => a.name.startsWith("data-") || ["href", "name", "aria-label"].includes(a.name)).map(a => `${a.name}=${a.value}`).sort();
    return [node.parentElement?.closest("[data-job]")?.dataset.job || "", node.tagName, node.tagName === "DETAILS" ? node.className : "", ...own].join("|");
  }
  function located(scope, selector) {
    const seen = new Map();
    return [...scope.querySelectorAll(selector)].map(node => {
      const key = identity(node), at = seen.get(key) || 0;
      seen.set(key, at + 1);
      return [`${key}#${at}`, node];
    });
  }
  function pageState() {
    const page = root(), active = document.activeElement;
    const focused = active && active !== document.body && page.contains(active) ? active : null;
    return {open: new Set(located(page, "details").filter(([, fold]) => fold.open).map(([key]) => key)), y: window.scrollY,
      tag: focused?.tagName || "", focus: focused ? located(page, focused.tagName).find(([, node]) => node === focused)?.[0] || "" : ""};
  }
  function restorePage(state, {scroll = true} = {}) {
    const page = root();
    for (const [key, fold] of located(page, "details")) if (state.open.has(key) && !fold.open) fold.open = true;
    const lost = !document.activeElement || document.activeElement === document.body;
    if (state.focus && lost) located(page, state.tag).find(([key]) => key === state.focus)?.[1].focus({preventScroll: true});
    if (scroll && Math.abs(window.scrollY - state.y) > 1) window.scrollTo({top: state.y, behavior: "instant"});
  }
  // A plan none of whose scans has cleared its scan checks yet (`entry.ready` false; item 12): one line over the steps saying what
  // waits and where, in place of the scan-checks notice it includes. Grading needs papers; preparing the rubrics does not.
  function entryNotice(plan) {
    if (!plan.entry || plan.entry.ready) return "";
    const pending = plan.workflow?.awaiting_review || plan.entry.pending_scans || 0;
    const what = pending ? `Waiting for scan checks on ${n(pending)} ${pending === 1 ? "scan" : "scans"}.` : "No scan of this assignment has cleared its scan checks yet.";
    const href = pending ? waitingScansHref(plan) : uploadScansHref(plan), label = pending ? SCAN_CHECKS_LABEL : "Upload scans";
    return `<aside class="grading-inbox-notice" data-scan-checks-notice data-grading-entry><p><strong>${h.e(what)}</strong> Rubrics, templates and answer areas can be prepared now; responses are graded once papers clear the checks.</p><a class="text-button" href="${h.e(href)}">${h.e(label)} →</a></aside>`;
  }
  // While a step is redrawn in place the page keeps its height, so nothing above or below it moves and the scroll is not cut short.
  // The returned release lets go once the new step is drawn (`settled`) and as tall as the old one, or after 3 s, or at the next
  // render; a step that is shorter for good then ends where its content ends.
  function holdHeight() {
    const page = root(), doc = document.documentElement;
    const held = page.offsetHeight, tall = doc.scrollHeight, y = window.scrollY;
    // The bottom margin the last child collapses through the page's bottom edge (a batch card's, inside the open Earlier batches):
    // with the min-height it stays inside the page, so it is held too; measured afterwards, it is lost only once the new step is
    // drawn shorter, too late to hold.
    let margin = 0;
    for (let node = page.lastElementChild; node; node = node.lastElementChild) {
      const style = getComputedStyle(node);
      margin = Math.max(margin, parseFloat(style.marginBottom) || 0);
      if (style.display !== "block" || style.overflow !== "visible" || parseFloat(style.paddingBottom) || parseFloat(style.borderBottomWidth)
        || (node.matches("details") && !(/** @type {HTMLDetailsElement} */ (node)).open)) break;
    }
    page.style.minHeight = `${held + margin}px`;
    // A min-height stops the last child's bottom margin collapsing through the page, which would make the document that much
    // shorter (and pull a page scrolled to its end up by it): hold that margin too, and undo the clamp measuring it caused.
    const lost = tall - doc.scrollHeight;
    if (lost > 0) {
      page.style.minHeight = `${held + margin + lost}px`;
      if (window.scrollY !== y) window.scrollTo({top: y, behavior: "instant"});
    }
    let observer = null, timer = null, done = false;
    const release = () => {
      if (done) return;
      done = true;
      observer?.disconnect(); clearTimeout(timer);
      if (heightHold === hold) heightHold = null;
      page.style.minHeight = "";
    };
    const natural = () => {
      const last = page.lastElementChild;
      return last ? last.getBoundingClientRect().bottom - page.getBoundingClientRect().top : 0;
    };
    const hold = (settled = false) => {
      if (!settled) { release(); return; }
      if (done || natural() >= held - 1) { release(); return; }
      if (observer) return;
      observer = new ResizeObserver(() => { if (natural() >= held - 1) release(); });
      for (const child of page.children) observer.observe(child);
      timer = setTimeout(release, 3000);
    };
    return hold;
  }
  // Gradescope from any step: the papers-only upload for a TA who grades there is needed right after scanning (GRADESCOPE),
  // so the bar links Certification and Export's Gradescope panel, which opens itself; nothing is sent from the link.
  function gradescopeLink(plan) {
    return `<a data-grading-gradescope-link href="#grading/${h.e(plan.id)}?tab=export&amp;open=gradescope">Gradescope</a>`;
  }
  function bindBar() {
    root().querySelector('[data-grading-action="refresh"]').onclick = () => { void render(true); };
    root().querySelector('[data-grading-action="settings"]').onclick = event => openSettings(event.currentTarget);
  }
  // Scroll a graph so the given step sits in the middle of its row (nothing when the row already fits).
  function centre(scroller, step) {
    if (!scroller || !step) return;
    const item = step.getBoundingClientRect(), frame = scroller.getBoundingClientRect();
    scroller.scrollLeft += item.left - frame.left - (scroller.clientWidth - item.width) / 2;
  }
  // Scan records (gsetup:GSP-16): an unnumbered utility. What arrived, scans still in the Inbox first, whole rows linking to them,
  // each named by its file with its pages and papers; readiness only where a count is not zero; one way on, to the assignment's
  // current step. Until a scan clears the Inbox no student is counted as missing a scanned paper, and while the assignment waits
  // for papers the one way on is the scan (uploading it, or finishing it in the Inbox), never "Continue to Grade".
  function papersView(host, plan, jobs = []) {
    const papers = plan.papers || {};
    const w = plan.workflow || {}, c = w.collection || {};
    const scans = [...(w.scans || [])].sort((a, b) => Number(a.inbox_ready) - Number(b.inbox_ready));
    const waiting = scans.filter(s => !s.inbox_ready).length;
    const when = value => new Date(value).toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"});
    // A scan of bubble sheets (no printed labels) matches no paper: its count is left out rather than read "0 papers".
    const file = s => s.filename ? `${s.filename}${s.files > 1 ? ` + ${plural(s.files - 1, "more file")}` : ""}` : "";
    const facts = s => [file(s), plural(s.pages || 0, "page"), s.papers ? plural(s.papers, "paper") : "", `received ${when(s.created_at)}`].filter(Boolean).join(" · ");
    const arrivals = scans.map(s => `<li><a class="grading-paper-row" href="#inbox/job/${h.e(s.job_id)}"><span><strong>${s.inbox_ready ? "Cleared for grading" : "Waiting for scan checks"}</strong><small>${h.e(facts(s))}</small></span><span class="link-arrow">${s.inbox_ready ? "Open the scan" : SCAN_CHECKS_LABEL}</span></a></li>`).join("");
    const summary = scans.length ? `${n(scans.length)} ${scans.length === 1 ? "scan" : "scans"}${waiting ? ` · ${n(waiting)} waiting for scan checks` : " · all cleared for grading"}` : "No scan is linked to this assignment yet.";
    const counts = [[c.complete, "complete reviewed", "success"], [(c.partial || 0) + (c.missing_pages || 0), "incomplete", "warning"], [c.needs_review, "need identity or submission review", "warning"]].filter(([value]) => Number(value) > 0);
    const issues = (c.partial || 0) + (c.missing_pages || 0) + (c.needs_review || 0);
    const next = assignmentNext(plan, jobs);
    // Waiting for papers, the primary is the waiting step's own action (Upload scans, or Finish the scan checks). With no scan
    // otherwise, uploading one (in Collect, I1) stays the primary and continuing to the current step is a plain button.
    const onward = next.waiting ? `<a class="button primary" href="${h.e(next.href)}">${h.e(next.label)}</a>`
      : scans.length ? `<a class="button primary" data-step-continue href="${h.e(next.href)}">Continue to ${h.e(next.current?.label || "Grade")}</a>`
      : `<a class="button primary" href="${h.e(uploadScansHref(plan))}">Upload scans</a>${next.current ? `<a class="button" data-step-continue href="${h.e(next.href)}">Continue to ${h.e(next.current.label)}</a>` : ""}`;
    // Scans still in their scan checks are listed on Collect (I2); a scan not yet associated with any assignment is found where
    // things arrive, the Inbox.
    const another = waiting ? `<a class="link-arrow" href="${h.e(waitingScansHref(plan))}">${SCAN_CHECKS_LABEL}</a>` : `<a class="link-arrow" href="#inbox">${scans.length ? "Find another scan in the Inbox" : "Find the scan in the Inbox"}</a>`;
    host.innerHTML = `<section class="grading-panel grading-scan-records"><h3>Scan records for ${h.e(plan.title)}</h3><p class="section-lead">${h.e(summary)}</p>
      <ul class="grading-paper-steps">${arrivals || '<li class="grading-paper-empty">Upload the scans once it is given, or find its scan in the Inbox and choose this assignment for it.</li>'}</ul><p>${another}</p>
      <h4>Papers</h4>${counts.length ? `<p class="grading-paper-counts">${counts.map(([value, label, tone]) => `<span class="chip" data-tone="${tone}">${h.e(n(value))} ${h.e(label)}</span>`).join(" ")}</p>` : '<p class="supporting-copy">No paper has been matched to a student yet.</p>'}
      ${papers.print_jobs_attached || papers.print_jobs_available ? `<p class="supporting-copy">${h.e(n(papers.print_jobs_attached))} print ${papers.print_jobs_attached === 1 ? "job" : "jobs"} attached${papers.print_jobs_available ? ` · ${h.e(n(papers.print_jobs_available))} available to attach on the Exams page` : ""}.</p>` : ""}
      <div class="grading-actions">${issues && plan.event_id ? `<a class="button" href="#exams/${h.e(encodeURIComponent(plan.event_id))}?tab=collection">Resolve ${h.e(n(issues))} collection ${issues === 1 ? "issue" : "issues"}</a>` : ""}${onward}</div></section>`;
  }
  function questionDialog(plan, question = null) {
    const value = question?.content || {title: "Question 1", max_points: 10, regions: [{page: 1, region: {x: .1, y: .2, width: .8, height: .6}, privacy_masks: []}]};
    const choices = plan.templates;
    const form = h.dialog(question ? "Edit question and solution" : "Define a question", "Question and solution revisions are retained. Changes require a newly approved rubric.", `${h.select("Printed document version", "document", plan.document_versions.map(v => [v.id, `${v.label} · ${v.page_count} pages`]), question?.assignment_version_id)}<div class="grading-columns">${h.field("Question number / key", "key", question?.question_key || "q1", "text", "required")}${h.field("Title", "title", value.title, "text", "required")}${h.field("Maximum points", "max", value.max_points, "number", 'min="0.01" step="0.01" required')}</div>${h.field("Printed question number", "printed_number", value.printed_number || "", "text", 'inputmode="numeric" maxlength="4"')}<p class="supporting-copy">Optional when the rubric filename names its printed question; the answer areas step checks it against the blank exam.</p>${h.area("Problem", "problem", value.problem || "")}${h.select("Blank template", "template", [["", "No template — boundaries require review"], ...choices.map(t => [t.id, t.label])], value.template_id || "")}${h.button("use-template", "Use saved template question regions")}<details><summary>Answer areas and privacy masks (advanced)</summary>${h.area("Answer areas (JSON)", "regions", JSON.stringify(value.regions, null, 2))}</details><p class="supporting-copy">Regions are page-relative x, y, width and height (0–1). Privacy masks are crop-relative [left, top, right, bottom]. The original scan stays intact. Answer areas are drawn and confirmed on the Answer areas step.</p>${h.area("Expected final answer", "expected_answer", value.expected_answer || "")}${h.area("Coordinator solution", "coordinator_solution", value.coordinator_solution || "")}<details><summary>Additional solutions and calibration examples</summary>${h.area("Instructor solution", "instructor_solution", value.instructor_solution || "")}${h.area("Worked solution", "worked_solution", value.worked_solution || "")}${h.area("Acceptable methods", "acceptable_methods", value.acceptable_methods || "")}${h.area("Notes", "notes", value.notes || "")}${h.area("Calibration examples (JSON)", "examples", JSON.stringify(value.examples || [], null, 2))}</details>`, "Save question", async (values, _, active) => {
      const content = {title: values.get("title"), printed_number: values.get("printed_number"), max_points: Number(values.get("max")), regions: JSON.parse(values.get("regions")), template_id: values.get("template") || null, examples: JSON.parse(values.get("examples"))};
      for (const key of ["problem", "expected_answer", "coordinator_solution", "instructor_solution", "worked_solution", "acceptable_methods", "notes"]) content[key] = values.get(key);
      await h.api(`/api/grading/assignments/${plan.id}/questions`, "POST", {version: plan.version, question_key: values.get("key"), assignment_version_id: values.get("document"), content});
      if (active()) { h.close(); await render(true); }
    });
    mountRegions(form, h);
    form.querySelector('[data-grading-action="use-template"]').onclick = event => h.run(event.currentTarget, async () => {
      if (!form.elements.template.value) throw new Error("Choose a blank template first.");
      const data = await h.api(`/api/assignment-templates/${form.elements.template.value}`);
      if (!form.isConnected) return;
      const questions = data.questions;
      const selected = questions.find(q => q.id === form.elements.key.value) || (questions.length === 1 ? questions[0] : null);
      if (!selected) throw new Error("Set the question key to one in the saved template mapping.");
      form.elements.regions.value = JSON.stringify(selected.regions || [{page: selected.page, region: selected.region}], null, 2);
      form.elements.regions.dispatchEvent(new Event("change"));
    });
  }
  return {render};
}
