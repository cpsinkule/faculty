// A scan's flow graph (requirement, 2026-09-24). The stages come from the server's overview of
// the scan (`ScanService.overview` → `stages`); this module only draws them with the grading workflow's step graph, and says which is
// current. It owns no data and decides nothing.
import { pipelineMarkup } from "./grading-guide.js";
import { nextStepMarkup } from "./ui.js";

// The scan state words (2026-10-18, one vocabulary across the Inbox and Assignments): Needs you, Working, Failed, Done, Not started.
// The server's stage statuses are those words; older ones still map onto them.
const ATTENTION = { "Needs you": "Needs you", "Needs a look": "Needs you", "To do": "Needs you", Working: "Working" };

/** The chip tone (components.css .chip[data-tone]) of a scan's derived state and of a job's stored status. A view only. */
export const SCAN_TONES = { todo: "warning", check: "warning", wait: "info", done: "success" };
export const STATUS_TONES = { pending: "info", running: "info", completed: "success", needs_review: "warning", failed: "danger", cancelled: "neutral" };
/** The stage page of a scan, or where a stage leads (grading). */
export const stageHref = (jobId, stage) => stage.href || (stage.page ? `#inbox/job/${jobId}/${stage.page}` : `#inbox/job/${jobId}`);

/** The stage that needs the instructor first, else the one the worker is on, else none. */
export function currentStage(stages = []) {
  return stages.find((stage) => stage.state === "started" && stage.urgent) || stages.find((stage) => stage.state === "started") || null;
}

/** The overview's stages in the shape the step graph draws. */
export function scanSteps(jobId, stages = [], current = currentStage(stages)) {
  return stages.map((stage) => {
    const progress = stage.progress && stage.progress.total > 0 ? {
      completed: stage.progress.completed, total: stage.progress.total, unit: stage.progress.unit,
      percent: Math.floor(100 * Math.min(stage.progress.completed, stage.progress.total) / stage.progress.total), explanation: "",
    } : null;
    return { key: stage.key, label: stage.label, title: stage.title, detail: stage.detail, status: stage.status,
      done: stage.state === "done", failed: stage.state === "failed", started: stage.state === "started", current: stage === current,
      destination: stageHref(jobId, stage), progress };
  });
}

/** The graph alone, a stage selected: the navigation at the top of each of a scan's pages. */
export function scanFlowNav(jobId, stages, selectedKey, escape) {
  // Where this page sits in the flow, in words under the graph (like grading's "Step 3 of 6").
  const index = stages.findIndex((stage) => stage.key === selectedKey);
  const position = index < 0 ? "" : `<p class="scan-flow-position" data-scan-flow-position>Stage ${index + 1} of ${stages.length} · <strong>${escape(stages[index].label)}</strong> · ${escape(stages[index].status)}</p>`;
  return `<nav class="scan-flow" aria-label="Scan workflow">${pipelineMarkup(scanSteps(jobId, stages), { e: escape }, selectedKey, "Scan workflow stages")}${position}</nav>`;
}

/** What the card's footer says: the stage to attend to, its words, and where its button leads. `goal` is the stage's goal in
 * words ("Find whose paper each is"), the words the card's button and the next-step banner share; `where` names the stage and its
 * state ("Next · Students"). `label` stays the stage's name. */
export function scanNext(jobId, stages = []) {
  const current = currentStage(stages);
  if (current) {
    const title = current.title.charAt(0).toLowerCase() + current.title.slice(1);
    const word = ATTENTION[current.status] || "Needs you";
    return { status: `${word}: ${title}`, detail: current.detail, label: current.label, href: stageHref(jobId, current), urgent: current.urgent,
      // While the worker has the stage, there is nothing to do there but watch it.
      goal: current.status === "Working" ? "View progress" : current.title, where: `${word} · ${current.label}` };
  }
  const grading = stages.find((stage) => stage.key === "grading" && stage.state === "done" && stage.href);
  const failed = stages.find((stage) => stage.state === "failed");
  if (failed) return { status: `Failed: ${failed.title.charAt(0).toLowerCase() + failed.title.slice(1)}`, detail: failed.detail, label: failed.label, href: stageHref(jobId, failed), urgent: true,
    goal: failed.title, where: `Failed · ${failed.label}` };
  // An arrival without a PDF is its Scan stage alone: say that, in the stage's own words, rather than "every stage is done".
  const lone = stages.length === 1 && stages[0].key === "scan" && stages[0].state === "done" ? stages[0] : null;
  return grading ? { status: "Nothing here needs you", detail: grading.detail, label: "Open grading", href: grading.href, urgent: false, goal: "Open grading", where: "Nothing here needs you" }
    : { status: "Nothing here needs you", detail: lone?.detail || "Every stage of this scan is done or waiting on an earlier one.", label: "Open scan", href: `#inbox/job/${jobId}`, urgent: false, goal: "Open scan", where: "Nothing here needs you" };
}

/**
 * The next step, on a stage page that is not where it is done (requirement, 2026-09-26): the stage that needs the instructor first, its words, and a button to its page.
 * Nothing when the page shown IS that stage, or when no stage needs anything. The words are the server's overview, escaped here.
 * @param {string} jobId @param {any[]} stages @param {string} here @param {(text: unknown) => string} escape
 */
export function scanNextStep(jobId, stages, here, escape) {
  const current = currentStage(stages);
  if (!current || current.key === here || current.status === "Working") return "";
  // The shared next-step callout (ui.js): the stage's goal as its title, the same words as the card's button.
  return nextStepMarkup({ eyebrow: `Next step · ${current.label}`, title: current.title, detail: current.detail, label: current.label, href: stageHref(jobId, current) })
    .replace('<aside class="next-step"', '<aside class="next-step" data-scan-next-step');
}

/**
 * A scan as a card, like an assignment card in Grading: what it is, its facts, the graph of its stages, and the one thing to do next.
 * `menu` is markup for the card's '⋯' actions (ui.js actionMenuMarkup), placed in its header; `actions` sit beside the next button.
 * `compact` draws the graph small (a finished scan in History).
 * @param {{jobId: string, overview: any, escape: (text: unknown) => string, facts?: string[], actions?: string, heading?: boolean, menu?: string, compact?: boolean}} options
 */
export function scanCard({ jobId, overview, escape, facts = [], actions = "", heading = true, menu = "", compact = false }) {
  const stages = overview.stages || [], next = scanNext(jobId, stages), done = stages.filter((stage) => stage.state === "done").length;
  const title = heading ? `<h3><a href="#inbox/job/${escape(jobId)}">${escape(overview.headline)}</a></h3>` : `<h3>${escape(overview.headline)}</h3>`;
  // On the scan's own page (no heading link), a next step that would only open this same page is left out.
  // A scan with nothing for the instructor whose button only opens it (no grading to open) gets a quiet button, not a primary. In a
  // list (a heading link) only a card that needs the instructor is ivory: 'Open grading' and 'View progress' are quiet there
  // (2026-10-02: 23 ivory 'Open grading' on Archived), while the scan's own page keeps its one primary.
  const quiet = next.goal === "Open scan" || (heading && !next.urgent);
  const button = !heading && next.href === `#inbox/job/${jobId}` ? "" : `<a class="button${quiet ? "" : " primary"}" data-scan-next-action href="${escape(next.href)}">${escape(next.goal)} →</a>`;
  const graph = pipelineMarkup(scanSteps(jobId, stages), { e: escape }, null, "Scan workflow stages");
  return `<article class="scan-card" data-scan-card data-scan-state="${escape(overview.state)}"><header><div class="grading-card-summary"><div class="grading-card-heading">${title}${overview.facts?.length ? `<span class="eyebrow">${escape(overview.facts.join(" · "))}</span>` : ""}</div>${facts.length ? `<div class="grading-card-facts">${facts.join("")}</div>` : ""}</div><div class="scan-card-corner"><span class="grading-card-completion">${done} of ${stages.length} stages done</span>${menu}</div></header>
    ${compact ? `<div data-size="compact">${graph}</div>` : graph}
    <footer><div><p class="grading-card-status" data-scan-next-status data-urgent="${next.urgent}">${escape(next.where)}</p><p class="grading-card-detail">${escape(next.detail)}</p></div><div class="scan-card-actions">${button}${actions}</div></footer></article>`;
}

const revealed = new WeakSet();
const watching = typeof ResizeObserver === "function"
  ? new ResizeObserver((entries) => { for (const entry of entries) reveal(entry.target); }) : null;
function reveal(scroller) {
  if (revealed.has(scroller) || !scroller.isConnected) return void watching?.unobserve(scroller);
  if (scroller.scrollWidth <= scroller.clientWidth + 1) return void watching?.observe(scroller);
  revealed.add(scroller);
  watching?.unobserve(scroller);
  const step = scroller.querySelector('a[aria-current="step"]')?.closest("li");
  if (!step) return;
  const box = scroller.getBoundingClientRect(), at = step.getBoundingClientRect();
  scroller.scrollLeft = Math.max(0, scroller.scrollLeft + at.left - box.left - (scroller.clientWidth - at.width) / 2);
}
// Graphs waiting to come near the screen. One that left the page first (a list drawn again) is let go at the next call.
const waiting = new Set();
const near = typeof IntersectionObserver === "function"
  ? new IntersectionObserver((entries) => {
    for (const entry of entries) if (entry.isIntersecting) { near.unobserve(entry.target); waiting.delete(entry.target); reveal(entry.target); }
  }, { rootMargin: "200px 0px" }) : null;
/**
 * A scan card's graph scrolled so its current stage is in view, once per graph (a narrow screen otherwise shows the first stages
 * while the one needing the instructor is off to the right). A graph that fits is watched until it no longer does (a window made
 * narrower); after its one scroll, the instructor's own scrolling is kept through redraws. A graph is looked at once it comes near
 * the screen (2026-10-02: measuring every graph of a list of 23 cards at once forced their layout, a quarter of a second, though
 * the Inbox list skips drawing cards off screen).
 * @param {ParentNode} root
 */
export function revealCurrentStage(root) {
  for (const scroller of waiting) if (!scroller.isConnected) { near?.unobserve(scroller); waiting.delete(scroller); }
  for (const scroller of root.querySelectorAll(".scan-card .grading-pipeline-scroll, .scan-flow .grading-pipeline-scroll")) {
    if (revealed.has(scroller) || waiting.has(scroller)) continue;
    if (near) { waiting.add(scroller); near.observe(scroller); } else reveal(scroller);
  }
}
