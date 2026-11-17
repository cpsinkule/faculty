// @ts-check
/**
 * The sidebar's Canvas page, `#canvas` (canvas/README.md "Finding it"): every current course's Canvas state from Faculty's own
 * records (GET /api/canvas/overview; Canvas is not contacted), the steps from linking the sections to pushing Faculty's copy to
 * every section, and ONE next step. It only links: each step is done on its own page (a section tab links its Canvas course,
 * the course's Canvas content page starts, edits and pushes the copy). Its one action is Message students (canvas-message.js,
 * canvas/README.md "Messaging sections"; `?message=1` opens it once), with the messages scheduled (MAIL-SEND-LATER) and sent listed
 * below the courses.
 */
import { createCanvasMessage, sentMarkup } from "./canvas-message.js";
import { handleScheduledClick, scheduledMarkup } from "./scheduled-sends.js";
import { calloutMarkup, emptyMarkup, loadingMarkup, nextStepMarkup } from "./ui.js";
import { patchChildren } from "./dom-patch.js";

const LEAD = "Edit your Canvas modules, pages and assignments in Faculty and push them to every section.";

/** The steps of one term, each {label, detail, state: done|started|todo}, and its next step (null when up to date).
 * @param {any} term one row of GET /api/canvas/overview
 */
export function canvasSteps(term) {
  const linked = term.sections.filter((/** @type {any} */ s) => s.course).length;
  const total = term.sections.length;
  const pushed = term.courses.filter((/** @type {any} */ c) => c.pushed).length;
  const courses = term.courses.length;
  const home = `#course/${encodeURIComponent(term.group)}/home/canvas`;
  const steps = [
    { label: "Link each section’s Canvas course", detail: `${linked} of ${total} section${total === 1 ? "" : "s"} linked`,
      state: linked === total ? "done" : linked ? "started" : "todo" },
    { label: "Start Faculty’s copy from one section", detail: term.copy ? `Revision ${term.copy.revision}` : "Not started",
      state: term.copy ? "done" : "todo" },
    { label: "Push to one section first and check it in Canvas", detail: pushed ? "Done" : "Not yet", state: pushed ? "done" : "todo" },
    { label: "Push to the other sections", detail: courses ? `${pushed} of ${courses} Canvas course${courses === 1 ? "" : "s"} pushed` : "",
      state: courses && pushed === courses ? "done" : pushed ? "started" : "todo" },
  ];
  const unlinked = term.sections.find((/** @type {any} */ s) => !s.course);
  const next = {
    link_sections: unlinked && { title: `Link Section ${unlinked.number}’s Canvas course`,
      detail: "On the section tab, the Canvas line of its work list offers Link a Canvas course.", label: `Open Section ${unlinked.number}`,
      href: `#course/${encodeURIComponent(term.group)}/section/${unlinked.number}` },
    start_copy: { title: "Start Faculty’s copy of your Canvas content",
      detail: "Choose the section whose Canvas course already looks the way every section should. Nothing changes in Canvas until you push.",
      label: "Start the copy", href: home },
    push_first: { title: "Push to one section first",
      detail: "The push window starts with one section ticked. Check that course in Canvas, then push to the rest.", label: "Open Canvas content", href: `${home}?push=1` },
    push_rest: { title: "Push to the other sections", detail: "One section has Faculty’s copy. Push it to the others once you have checked it in Canvas.",
      label: "Open Canvas content", href: `${home}?push=1` },
  }[term.next] || null;
  return { steps, next };
}

/** @param {{api: any, escape: (v: any) => string, header?: any, toast?: (m: string, o?: any) => void, showDialog?: (html: string) => void,
 *  dialogHeader?: (t: string, s?: string) => string}} deps */
export function createCanvasHub({ api, escape, header = null, toast = () => {}, showDialog = () => {}, dialogHeader = (t) => t }) {
  const e = escape;
  const root = () => document.getElementById("canvas-section");
  const active = () => location.hash.split("?")[0] === "#canvas";
  /** @type {any} */ let data = null;
  /** @type {any[]} */ let sent = [];
  /** @type {any[]} */ let later = [];
  let error = "", written = "", generation = 0, readAt = 0;
  // The read under way: the shell renders the page twice per navigation, and an unforced render joins it (FRONTEND.md
  // "One read per navigation"); the overview and the messages were each read twice.
  /** @type {Promise<void> | null} */
  let loading = null;
  /** @type {ReturnType<typeof setTimeout> | null} */ let following = null;
  const messages = createCanvasMessage({ api, escape, toast, showDialog, dialogHeader, onSent: () => { void render(true); } });

  function paintHeader() {
    if (!active()) return;
    const action = data?.canvas?.configured ? { label: "Message students", icon: "mail", run: () => { void messages.open(); } } : null;
    header?.set({ route: "#canvas", title: "Canvas", subtitle: LEAD, crumbs: [{ label: "Home", href: "#overview" }, { label: "Canvas" }], action });
  }

  async function render(force = false) {
    if (!active()) return;
    // `?message=1` (the palette's Message students through Canvas) opens the window once, then leaves the address.
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    if (params.get("message") === "1") {
      history.replaceState(history.state, "", "#canvas");
      void messages.open();
    }
    paintHeader();
    const node = root();
    if (!node) return;
    if (!data && !error) node.innerHTML = loadingMarkup("Reading your courses’ Canvas links…", 2);
    else draw();
    // Faculty's records change only when the instructor links, starts or pushes: read again on a visit after half a minute.
    if (data && !force && Date.now() - readAt < 30000) return;
    if (!force && loading) return loading;
    const promise = readOverview();
    loading = promise;
    try { await promise; } finally { if (loading === promise) loading = null; }
  }

  async function readOverview() {
    const run = ++generation;
    readAt = Date.now();
    try {
      const [read, mail, kept] = await Promise.all([api("/api/canvas/overview"), api("/api/canvas/messages").catch(() => null),
        api("/api/scheduled-sends?channel=canvas").catch(() => null)]);
      if (run !== generation) return;
      data = read;
      sent = mail?.sent || sent;
      later = kept ? kept.scheduled.filter((/** @type {any} */ item) => (item.state !== "sent" && item.state !== "cancelled") || item.problem) : later;
      error = "";
      // A message still being sent: read the list again shortly, while this page is shown.
      if (following) clearTimeout(following);
      following = sent.some((r) => r.status === "sending") ? setTimeout(() => { following = null; void render(true); }, 3000) : null;
    } catch (failure) {
      if (run !== generation) return;
      error = /** @type {any} */ (failure)?.message || "The Canvas overview could not be read.";
    }
    paintHeader();
    draw();
  }

  function stepsMarkup(steps) {
    const chip = { done: ["success", "Done"], started: ["warning", "Under way"], todo: ["neutral", "To do"] };
    return `<ol class="canvas-hub-steps">${steps.map((/** @type {any} */ s) => `<li data-state="${e(s.state)}"><span class="canvas-hub-step">${e(s.label)}</span>
      <span class="canvas-hub-detail">${e(s.detail)}</span><span class="chip" data-tone="${chip[s.state][0]}">${chip[s.state][1]}</span></li>`).join("")}</ol>`;
  }

  function termMarkup(term, url) {
    const { steps, next } = canvasSteps(term);
    const group = encodeURIComponent(term.group);
    const content = `#course/${group}/home/canvas`;
    const pushed = new Set(term.courses.filter((/** @type {any} */ c) => c.pushed).map((/** @type {any} */ c) => c.course_id));
    // A course is marked only when it is the exception: one a push has not reached yet while others have the copy.
    const rows = term.sections.map((/** @type {any} */ s) => {
      const course = s.course;
      const place = course
        ? `${url ? `<a href="${e(url)}/courses/${e(course.course_id)}/modules" target="_blank" rel="noopener noreferrer">${e(course.name || `Canvas course ${course.course_id}`)} ↗</a>` : e(course.name)}
           ${pushed.size && !pushed.has(course.course_id) ? '<span class="chip" data-tone="warning">No push yet</span>' : ""}
           <a class="text-button" href="#course/${group}/section/${e(s.number)}/canvas">Edit only this section</a>`
        : `<span class="canvas-hub-missing">No Canvas course linked</span> <a class="text-button" href="#course/${group}/section/${e(s.number)}">Link one on the section tab</a>`;
      return `<li><strong>Section ${e(s.number)}</strong><span class="canvas-hub-place">${place}</span></li>`;
    }).join("");
    // Once every step is done the steps are finished work: folded (DESIGN-LANGUAGE §1.7), and the way into the content leads.
    const finished = steps.filter((/** @type {any} */ s) => s.state === "done").length;
    const setup = next || finished < steps.length ? `${next ? nextStepMarkup(next) : ""}${stepsMarkup(steps)}`
      : `<details class="disclosure canvas-hub-setup"><summary><span>Setup</span><span class="disclosure-hint">${finished} of ${steps.length} steps done</span></summary><div class="disclosure-body">${stepsMarkup(steps)}</div></details>`;
    return `<section class="card canvas-hub-term" data-patch-key="term-${e(term.group)}" aria-label="${e(`${term.term.code} · ${term.term.term_label}`)}">
      <header class="canvas-hub-title"><h2>${e(term.term.code)} <small>${e(term.term.term_label)}</small></h2>
        <a class="button" href="${content}">Modules, pages and assignments</a></header>
      <ul class="canvas-hub-sections">${rows}</ul>
      ${setup}
    </section>`;
  }

  function draw() {
    const node = root();
    if (!node) return;
    let markup;
    if (error) markup = calloutMarkup({ tone: "danger", title: "Canvas could not be shown", body: error });
    else if (!data) return;
    else if (!data.canvas?.configured) markup = calloutMarkup({ tone: "warning", title: "Canvas is not set up for this Faculty instance" });
    else if (!data.terms.length) markup = emptyMarkup({ icon: "route", title: "No current courses", body: "Canvas content belongs to a current course term. Mark a course current on its settings to see it here." });
    else markup = data.terms.map((/** @type {any} */ t) => termMarkup(t, data.canvas.url)).join("");
    if (data?.canvas?.configured) markup += scheduledMarkup(later, e, { title: "Scheduled Canvas messages", channel: false }) + sentMarkup(sent, e);
    const full = `<div class="canvas-hub">${markup}</div>`;
    if (full === written && node.firstElementChild) return;
    written = full;
    patchChildren(node, full);
    /** @type {HTMLElement} */ (node).onclick = (event) => {
      if (handleScheduledClick(event, { api, toast, showDialog, dialogHeader, escape: e, items: () => later, onChange: () => { void render(true); } })) return;
      const button = /** @type {HTMLElement | null} */ (event.target instanceof Element ? event.target.closest("[data-cm-check]") : null);
      const record = button && sent.find((r) => r.id === button.dataset.cmCheck);
      if (record) messages.details(record);
    };
  }

  return { render };
}
