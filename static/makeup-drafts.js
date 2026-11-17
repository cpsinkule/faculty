// @ts-nocheck
/* Makeup versions drafted by a model (requirement, 2026-10-03; grading/makeup_drafts.py, GRADING "MAKEUP VERSIONS").

   Draft makeup version… (the Absences page's Makeups view, the assignment card's menu) opens this window for one grading plan: base
   the makeup on one of its versions and, optionally, tell the model what to keep in mind; the model writes ONE parallel version (the
   same problems, points and steps, new numbers and contexts) and adapts that version's rubrics to it line by line. The window shows
   the printed pages, each problem's adapted rubric beside the original's lines, and what to look at; ask for changes, or approve it:
   the makeup becomes the assignment's next version, graded in the same plan, printed as one unnamed job attached to a makeup sitting,
   with the students ticked placed on that sitting. A makeup draft is a quiz draft, so revising, stopping, scrapping, its files and
   approving use the quiz-draft routes. Each problem shows the answer key read for it while the makeup was drafted, and the approval
   sent from this window (`save_keys`) saves those keys with its questions (requirement, 2026-10-11).
   Nothing is stored here; every write is an ordinary request the server checks. */
import { mathText, renderMath } from "./grading-math.js";
import { answerMarkup, checksMarkup, countMarkup, cropMarkup, keyBasisMarkup, keyCounts, keyReadMarkup, problemCrop, proseMarkup, reviewNotes, saidMarkup, wholeMarkup } from "./review-notes.js";
import { dialogSession, disclosureMarkup, errorMessage, loadingMarkup, plural, runFormAction, scrollBehavior, wallDate } from "./ui.js";
import { viewLink } from "./file-viewer.js";
import { sittingText } from "./absence-makeups.js";

const draftBase = (id) => `/api/quiz-drafts/${encodeURIComponent(id)}`;

/** Which makeup draft the window is about: the open one (drafting, ready or approving), else none. @param {any[]} drafts */
export function openDraft(drafts) {
  return (drafts || []).find((d) => d.state !== "scrapped" && d.state !== "approved") || null;
}

/** What the model is doing on a live revision. @param {any} revision */
export function makeupProgress(revision) {
  const p = revision?.progress || {};
  if (!p.written) return "Writing the makeup from the original version and its rubrics…";
  if (!p.checked) return p.fixes ? "Fixing what did not compile or fit on its page…" : "Compiling the makeup and checking its printed pages…";
  if (p.fixing) return "Fixing what the check of the printed pages found…";
  if (p.keys) return "Reading each problem’s answer key from its solution…";  // the last step: after the rubrics (2026-10-11)
  if (p.problems) return `Adapting the rubrics: ${Math.min(p.rubrics, p.problems)} of ${p.problems} problems…`;
  return "Adapting the rubrics…";
}

// READABLE REVIEW (requirement, 2026-10-05):
// review-notes.js sorts the revision's notes and warnings under their problem, shown beside the problem as printed.

// A makeup sitting in one line, its date as the campus wall date ("Tue, Oct 6", never "2026-10-06"), each part said once.
export { sittingText };

/**
 * What a student's row in the approval window says, one short line (requirement, 2026-11-01): where they sit now, else their booking card, else that their absence waits for a decision.
 * KEEP THEIR SITTING: `chosen` is the sitting picked above ("new" for a new one, "" for none); a ticked student on another sitting
 * says that approving moves them.
 * @param {any} row @param {string} chosen @param {boolean} ticked
 */
export function studentStatus(row, chosen, ticked) {
  const s = row.scheduled;
  if (s?.sitting_id) {
    if (s.sitting_id === chosen) return "Already on this sitting";
    if (ticked && chosen) return `Moves ${chosen === "new" ? "to the new sitting" : "here"} from ${sittingText(s)}`;
    return `${row.booking?.state === "booked" ? "Booked" : "On"} ${sittingText(s)}`;
  }
  if (row.state === "if_excused") return "Absence not decided yet";
  // MAKEUP HOLDS: a held makeup says what it waits on, as the Makeups view does.
  if (row.hold?.held) return `Held${row.hold.reason ? `: waiting on ${row.hold.reason}` : " until you release it"}`;
  return row.booking?.label || row.state_label || "Makeup owed";
}

/**
 * What approving does as the approval form stands (requirement, 2026-10-06): the version it
 * adds, the copies it prints, where the ticked students are placed, and what still waits. Plain text.
 * KEEP THEIR SITTING (requirement, 2026-10-06, after an approval moved Gina off the makeup sitting she had agreed to): `moving` counts
 * the ticked students it takes off another makeup sitting.
 * ANSWER KEYS (requirement, 2026-10-11): `keys` (review-notes.js `keyCounts`) says how many answer keys
 * approving saves, the ones shown beside the problems; null for a revision that read none, which reads as before.
 * @param {{letter: string, title: string, copies: number, sitting: any, students: number, booked?: boolean, moving?: number, keys?: {read: number, missing: number}|null}} form
 */
export function approveSummary({ letter, title, copies, sitting, students, booked = false, moving = 0, keys = null }) {
  // With booking cards on (`booked`), approving is also what a booked student's copy waits for (ABSENCES "COPIES").
  const prints = copies > 0 ? `prints ${copies} unnamed ${copies === 1 ? "copy" : "copies"}` : booked ? "prints no copy now" : "prints nothing";
  const where = !sitting ? "" : sitting.new ? `a new makeup sitting${sitting.date ? ` on ${wallDate(sitting.date)}` : ""}`
    : sittingText(sitting);
  const placed = booked && !students ? "; students book their own time on their card, and each booked student gets a copy"
    : students && where ? `, and places ${students} ${students === 1 ? "student" : "students"} on ${where}`
      + (moving ? `, moving ${moving} off another makeup sitting` : "")
    : where ? `, for ${where}` : "";
  const saved = !keys ? "" : keys.read ? ` It saves the ${keys.read === 1 ? "answer key" : `${keys.read} answer keys`} shown${keys.missing ? ` (${keys.missing} could not be read)` : ""}.`
    : " No answer key could be read, so none is saved.";
  return `Approving adds Version ${letter} to ${title} in the same grading plan, ${prints}${placed}.${saved} Finalize its rubrics in Key & Rubric Finalization before grading.`;
}

/**
 * @param {{api: Function, escape: (v: unknown) => string, toast: Function, showDialog: (markup: string) => void, dialogHeader: Function}} deps
 * @param {{planId: string, title?: string}} target
 */
export async function openMakeupDrafts({ api, escape, toast, showDialog, dialogHeader }, { planId, title = "" }) {
  const e = (v) => escape(String(v ?? ""));
  const h = { e: escape };
  const dialog = () => document.getElementById("dialog");
  const url = `/api/grading/assignments/${encodeURIComponent(planId)}/makeups`;
  let timer = null, options = null, draft = null, shown = null;
  // The window's two ways on stay in its footer (2026-10-06: they sat ~2000 px down, under every problem); each brings its form into view.
  showDialog(`<div class="absence-form" id="makeup-drafts">${dialogHeader("Draft makeup version", e(title))}<div class="dialog-body" data-makeup-body>${loadingMarkup("Reading the assignment…", 3)}</div>
    <footer class="dialog-footer"><button type="button" class="text-button" data-makeup-jump="makeup-changes" hidden>Ask for changes</button><button type="button" class="button" data-action="close">Close</button><button type="button" class="button primary" data-makeup-jump="makeup-approve" hidden>Approve…</button></footer></div>`);
  const root = document.getElementById("makeup-drafts");
  const session = dialogSession(root, dialog());
  const body = () => root.querySelector("[data-makeup-body]");

  async function load() {
    clearTimeout(timer);
    if (!session()) return;
    try {
      options = await api(url);
      const open = openDraft(options.drafts);
      draft = open ? await api(draftBase(open.id)) : null;
    } catch (cause) {
      if (session()) body().innerHTML = `<div class="callout" data-tone="danger" role="alert"><p>${e(errorMessage(cause))}</p></div>`;
      return;
    }
    if (!session()) return;
    draw();
    if (draft && (draft.live || draft.state === "approving")) timer = setTimeout(load, 5000);
  }

  function approvedMarkup() {
    const done = (options.drafts || []).filter((d) => d.state === "approved" && d.approved?.result);
    if (!done.length) return "";
    return `<section class="absence-section"><h3>Makeup versions made</h3><ul class="absence-docs">${done.map((d) => {
      const r = d.approved.result;
      return `<li>Version ${e(r.letter)} <small>${e(plural(r.batches.length, "print job"))}${r.placed ? ` · ${e(plural(r.placed, "student"))} placed on its sitting` : ""}</small>
        ${r.batches.length ? `<a ${viewLink(`${draftBase(d.id)}/approvals/${d.approved.id}/stack`, `Makeup Version ${r.letter}`, escape)}>Print file (PDF)</a>` : ""}
        <a href="#grading/${encodeURIComponent(r.plan_id)}?tab=answer-key">Key &amp; Rubric Finalization</a>
        ${(r.notes || []).map((n) => `<small>${e(n)}</small>`).join("")}</li>`;
    }).join("")}</ul></section>`;
  }

  function createMarkup() {
    if (options.problem) return `<div class="callout" data-tone="warning"><p>${e(options.problem)}</p></div>${approvedMarkup()}`;
    if (options.model_problem) return `<div class="callout" data-tone="warning"><p>${e(options.model_problem)}</p></div>${approvedMarkup()}`;
    return `<form id="makeup-create" class="absence-form" novalidate>
      <p>The model writes Version ${e(options.letter)} of ${e(options.title)}: the same problems, points and steps as the version you choose, with new numbers and contexts that no version students have seen uses, and adapts that version's rubrics to it line by line. It takes about ten minutes; nothing is printed or graded until you approve it.</p>
      <label class="field">Based on<select name="source">${options.sources.map((s) => `<option value="${e(s.key)}">${e(s.label)} · ${e(plural(s.problems, "problem"))} · ${e(s.points)} points</option>`).join("")}</select></label>
      <label class="field">Instructions for the model <span class="field-optional">optional</span><textarea name="instructions" rows="3" maxlength="8000" placeholder="e.g. Use a different context in the word problem; keep the numbers small."></textarea></label>
      <p class="field-hint">Model: ${e(options.model)}.</p>
      <p class="form-error" role="alert" hidden></p>
      <div class="makeup-group-actions"><button type="submit" class="button primary">Draft makeup Version ${e(options.letter)}</button></div></form>${approvedMarkup()}`;
  }

  function latest() {
    return draft.revisions.find((r) => r.id === draft.latest_complete) || null;
  }

  // A REFUSED APPROVAL SAYS WHY (requirement, 2026-11-01): the worker refused it (Version A's Question 2 rubric changed after the makeup was drafted), the draft read "ready" again
  // and the window redrew its form with no word. The latest revision's last approval, when it failed, is said at the top and beside
  // Approve; a rubric changed since is adapted again with one press (a new revision, reviewed and approved as any other).
  function refused() {
    const revision = latest();
    const last = (draft.approvals || []).filter((a) => a.revision_id === revision?.id).at(-1);
    return last?.status === "failed" ? last : null;
  }

  function refusedMarkup() {
    const a = refused();
    if (!a) return "";
    const adapt = !draft.live && /changed after the makeup was drafted/.test(a.error || "");
    return `<div class="callout" data-tone="danger" role="alert"><p><strong>Approving did not go through.</strong> ${e(a.error)}</p>
      ${adapt ? '<button type="button" class="button" data-makeup-readapt>Adapt the rubrics again</button>' : ""}</div>`;
  }

  function statusMarkup() {
    const live = draft.revisions.find((r) => r.id === draft.live);
    const last = draft.revisions[draft.revisions.length - 1];
    if (draft.approved) {
      const a = draft.approved;
      if (a.status === "failed") return `<div class="callout" data-tone="danger" role="alert"><p><strong>Carrying out the approval stopped.</strong> ${e(a.error)}</p></div>`;
      return `<div class="callout" data-tone="info" role="status"><p><strong>Approved.</strong> Faculty is adding Version ${e(draft.makeup.letter)} to the assignment, its grading setup and its print job…</p></div>`;
    }
    if (live) return `<div class="callout" data-tone="info" role="status"><p><strong>Revision ${live.number}</strong>: ${e(makeupProgress(live))} This window updates by itself.</p>
      <button type="button" class="button" data-makeup-stop="${e(live.id)}">Stop</button></div>`;
    if (last && last.status !== "complete") return `<div class="callout" data-tone="danger" role="alert"><p><strong>Revision ${last.number} ${last.status === "cancelled" ? "was stopped" : "could not be finished"}.</strong> ${e(last.error || "")}</p>
      <button type="button" class="button" data-makeup-retry>Try again</button></div>`;
    return "";
  }

  function revisionMarkup(revision) {
    const result = revision.result;
    const letter = draft.makeup.letter;
    const version = result.versions[letter];
    const pdf = `${draftBase(draft.id)}/revisions/${encodeURIComponent(revision.id)}/versions/${letter}/pdf`;
    const src = (n) => `${draftBase(draft.id)}/revisions/${encodeURIComponent(revision.id)}/versions/${letter}/pages/${n}.png`;
    const name = `Makeup Version ${letter}`;
    const notes = reviewNotes(result);
    const pageOf = (n) => version.pages.find((p) => p.number === n);
    const problems = result.quiz.problems.map((problem) => {
      const drafted = result.rubrics.find((r) => r.problem === problem.number) || {};
      const rubric = drafted.rubric;
      const before = Object.fromEntries((rubric?.reference_items || []).map((line) => [line.id, line]));
      const content = problem.content[letter] || {};
      const about = notes.problems[problem.number] || { checks: [], writer: "", rubric: "" };
      const crop = problemCrop(version.layout?.problems, problem.number);
      const picture = cropMarkup({ crop, page: crop && pageOf(crop.page), pdf, name, src: crop ? src(crop.page) : "", number: problem.number, escape });
      const lines = rubric ? `<ol class="review-lines">${rubric.items.map((line) => {
        const original = before[line.id]?.description || "";
        return `<li><span class="review-line-id">${e(line.id)}</span><span class="review-line-points">${e(line.points)}</span>
          <div>${mathText(line.description, h)}<small>${original === line.description ? `Same wording as ${e(draft.makeup.source)}` : original ? `${e(draft.makeup.source)}: ${mathText(original, h)}` : `New line`}</small></div></li>`;
      }).join("")}</ol>` : "";
      const key = answerMarkup(problem, content, h);
      // The answer key read for the makeup while it was drafted (2026-10-11): what approving saves with this problem's question.
      const reading = drafted.keys?.[letter];
      const solution = `<h5>Solution</h5><div>${mathText(content.solution || rubric?.solution || "", h)}</div>
        ${(content.final_answers || []).length ? `<ul>${content.final_answers.map((a) => `<li>${a.part ? `(${e(a.part)}) ` : ""}${mathText(a.answer, h)}</li>`).join("")}</ul>` : ""}${keyBasisMarkup(reading, h)}`;
      return `<article class="review-problem${picture ? "" : " review-problem-plain"}" data-makeup-problem="${e(problem.number)}">${picture}
        <div class="review-problem-notes">
          <h4>Problem ${e(problem.number)} <span>${e(plural(problem.points, "point"))}${problem.tag ? ` · ${e(problem.tag)}` : ""}${crop ? ` · page ${crop.page}` : ""}</span></h4>
          ${checksMarkup(about.checks, h)}
          ${saidMarkup([["Makeup writer", about.writer], ["Rubric writer", about.rubric]], h, [["Answer", key], ["Answer key", keyReadMarkup(reading, h)]])}
          ${rubric ? disclosureMarkup({ label: "Rubric and solution", hint: plural(rubric.items.length, "line"), body: lines + solution, className: "review-fold" })
            : `<div class="callout" data-tone="warning"><p>No rubric yet: ${e(drafted.error || "ask for changes to adapt it again.")}</p></div>`}
        </div></article>`;
    }).join("");
    return `<section class="makeup-summary">
        <div>
          <h3>Version ${e(letter)} of ${e(options.title)}, parallel to ${e(draft.makeup.source)}</h3>
          <p class="review-meta">Revision ${revision.number} · ${e(plural(result.quiz.problems.length, "problem"))} · ${e(result.quiz.total)} points · ${e(plural(version.page_count, "page"))}</p>
          ${countMarkup(notes, result.quiz.problems.map((p) => p.number), e)}
          ${checksMarkup(notes.whole.checks, h)}
          ${result.changes ? `<div class="review-changed"><h5>What changed in revision ${revision.number}</h5>${proseMarkup(result.changes, h)}</div>` : ""}
          ${wholeMarkup(notes.whole.notes, h)}
        </div>
        <div class="makeup-pages"><div>${version.pages.map((p) => `<a ${viewLink(pdf, name, escape, p.number)} aria-label="Open page ${p.number} larger"><img src="${src(p.number)}" alt="${e(name)}, page ${p.number}" loading="lazy" width="${p.width_px}" height="${p.height_px}"><span>Page ${p.number}</span></a>`).join("")}</div>
          <a class="link-arrow" ${viewLink(pdf, name, escape)}>Open the makeup (PDF)</a></div>
      </section>
      <section class="review-problems" aria-label="Problems">${problems}</section>`;
  }

  function actionsMarkup() {
    if (draft.approved && draft.approved.status !== "failed") return "";
    // A certified assignment says so in a warning of its own, and the Approve button names what it does: pressing it is the
    // confirmation (`certified_ok`). A separate box to tick first was missed, so Approve seemed to do nothing (requirement, 2026-11-01).
    const certified = options.certified ? `<div class="callout makeup-certified" data-tone="warning"><p><strong>${e(options.title)} is already certified.</strong> Approving brings it back to Certify so the makeup papers can be graded. No existing grade changes.</p></div>` : "";
    return `<div class="makeup-decision"><form id="makeup-approve" class="absence-form absence-section" novalidate><h3>Approve</h3>${refusedMarkup()}
        <p class="makeup-approve-summary" data-approve-summary></p>
        <label class="field" data-copies-field hidden>Copies to print<input type="number" name="copies" min="0" max="500" value="1"></label>
        <fieldset class="absence-decision"><legend>Makeup sitting</legend><div class="makeup-sittings" data-makeup-sittings>${loadingMarkup("Reading the exam record…", 1)}</div></fieldset>
        <fieldset class="absence-decision"><legend data-students-legend>Students</legend><div data-makeup-students>${loadingMarkup("Reading the makeups owed…", 1)}</div></fieldset>
        ${certified}
        <p class="form-error" role="alert" hidden></p>
        <div class="makeup-group-actions"><button type="submit" class="button primary">Approve Version ${e(draft.makeup.letter)}${options.certified ? ` and bring ${e(options.title)} back to Certify` : ""}</button><button type="button" class="text-button" data-makeup-scrap>Scrap this draft…</button></div></form>
      <form id="makeup-changes" class="absence-form absence-section" novalidate><h3>Or ask for changes</h3>
        <label class="field">What should change?<textarea name="feedback" rows="4" maxlength="6000" placeholder="e.g. Problem 3 needs more room to write; use another context in problem 2."></textarea></label>
        <p class="form-error" role="alert" hidden></p><div class="makeup-group-actions"><button type="submit" class="button">Ask the model for these changes</button></div></form></div>`;
  }

  function draw() {
    if (!draft) {
      // No decision form is left to watch or to bring into view: the observer would report the removed form as out of view and
      // bring the footer's two ways back, with nothing for them to open.
      shown?.disconnect();
      shown = null;
      root.querySelectorAll("[data-makeup-jump]").forEach((button) => { button.hidden = true; });
      body().innerHTML = createMarkup();
      bindCreate();
      return;
    }
    const revision = latest();
    body().innerHTML = `${statusMarkup() || refusedMarkup()}${revision?.result ? revisionMarkup(revision) + actionsMarkup() : ""}`;
    renderMath(body());
    bindDraft(revision);
    const decision = root.querySelector(".makeup-decision");
    const ready = Boolean(root.querySelector("#makeup-approve"));
    // While the two forms are on screen the footer offers no second Approve: one way to each, where it is seen.
    let seen = false;
    shown?.disconnect();
    shown = decision && "IntersectionObserver" in window ? new IntersectionObserver(([entry]) => {
      seen = entry.isIntersecting;
      root.querySelectorAll("[data-makeup-jump]").forEach((button) => { button.hidden = !ready || seen; });
    }, { root: body(), threshold: 0.25 }) : null;
    if (shown) shown.observe(decision);
    root.querySelectorAll("[data-makeup-jump]").forEach((button) => {
      button.hidden = !ready || seen;
      if (button.dataset.makeupJump === "makeup-approve") button.textContent = `Approve Version ${draft.makeup.letter}…`;
      button.onclick = () => {
        const form = root.querySelector(`#${button.dataset.makeupJump}`);
        form?.scrollIntoView({ block: "start", behavior: scrollBehavior() });
        form?.querySelector("textarea, input:not([type=hidden])")?.focus({ preventScroll: true });
      };
    });
  }

  function bindCreate() {
    const form = root.querySelector("#makeup-create");
    if (!form) return;
    let requestId = "";
    form.addEventListener("input", () => { requestId = ""; });
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        requestId ||= crypto.randomUUID();
        await api(url, "POST", { request_id: requestId, source: form.elements.source.value, instructions: form.elements.instructions.value });
        toast(`Drafting makeup Version ${options.letter}`);
        await load();
      });
    };
  }

  function bindDraft(revision) {
    root.querySelector("[data-makeup-stop]")?.addEventListener("click", async (event) => {
      try { await api(`${draftBase(draft.id)}/revisions/${encodeURIComponent(event.currentTarget.dataset.makeupStop)}/cancel`, "POST", {}); } catch (cause) { toast(errorMessage(cause), true); }
      await load();
    });
    root.querySelector("[data-makeup-retry]")?.addEventListener("click", async () => {
      try { await api(`${draftBase(draft.id)}/revisions`, "POST", { request_id: crypto.randomUUID(), based_on: draft.latest_complete || null }); } catch (cause) { toast(errorMessage(cause), true); }
      await load();
    });
    // Scrapping asks once more in place: the draft can no longer be revised or approved afterwards.
    root.querySelector("[data-makeup-scrap]")?.addEventListener("click", (event) => {
      const button = event.currentTarget;
      button.insertAdjacentHTML("afterend", `<span class="callout" data-tone="warning" role="alert" data-makeup-scrap-ask>Scrap this makeup draft? It can no longer be revised or approved.
        <button type="button" class="button danger" data-makeup-scrap-yes>Scrap draft</button><button type="button" class="button" data-makeup-scrap-no>Keep it</button></span>`);
      button.hidden = true;
      const ask = root.querySelector("[data-makeup-scrap-ask]");
      ask.querySelector("[data-makeup-scrap-no]").onclick = () => { ask.remove(); button.hidden = false; button.focus(); };
      ask.querySelector("[data-makeup-scrap-yes]").onclick = async () => {
        try { await api(`${draftBase(draft.id)}/scrap`, "POST", { confirmation: "scrap" }); toast("Makeup draft scrapped"); } catch (cause) { toast(errorMessage(cause), true); }
        await load();
      };
      ask.querySelector("[data-makeup-scrap-no]").focus();
    });
    root.querySelectorAll("[data-makeup-readapt]").forEach((button) => button.addEventListener("click", async () => {
      root.querySelectorAll("[data-makeup-readapt]").forEach((b) => { b.disabled = true; });
      try {
        await api(`${draftBase(draft.id)}/revisions`, "POST", { request_id: crypto.randomUUID(), based_on: revision.id,
          feedback: `Approving stopped: ${refused()?.error || ""} Adapt that rubric again to the original's current lines; keep everything else as it is.` });
        toast("Asked the model to adapt the rubrics again");
      } catch (cause) { toast(errorMessage(cause), true); }
      await load();
    }));
    const changes = root.querySelector("#makeup-changes");
    if (changes) changes.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(changes, async () => {
        const feedback = changes.elements.feedback.value.trim();
        if (!feedback) throw new Error("Say what should change.");
        await api(`${draftBase(draft.id)}/revisions`, "POST", { request_id: crypto.randomUUID(), based_on: revision.id, feedback });
        toast("Asked the model for the changes");
        await load();
      });
    };
    const approve = root.querySelector("#makeup-approve");
    if (approve) void fillApproval(approve, revision);
  }

  async function fillApproval(form, revision) {
    let exam = null, owed = [], booking = null;
    try {
      let listed;
      [exam, listed] = await Promise.all([api(`/api/exams/${encodeURIComponent(options.event_id)}`), api("/api/absences/makeups?scope=all").catch(() => ({}))]);
      owed = listed.makeups || [];
      booking = listed.booking || null;
    } catch (cause) {
      form.querySelector("[data-makeup-sittings]").innerHTML = `<p class="form-error">${e(errorMessage(cause))}</p>`;
      return;
    }
    if (!form.isConnected) return;
    const sittings = (exam.sittings || []).filter((s) => s.kind === "makeup");
    const mine = owed.filter((row) => row.event_id === options.event_id);
    // MAKEUP BOOKING: when the quiz's term books makeups by card, students pick their own time and each gets a copy (named, 2026-10-18)
    // as soon as they book (COPIES, 2026-10-12: those already booked, as soon as this version is approved); approving then prints
    // no unnamed copy and places no one, so "None" comes first and the students are a list to read, not to tick (2026-11-01).
    // Choosing a sitting still lets you place students on it yourself.
    const term = mine[0]?.term_id || exam.term_id;
    const booked = Boolean(booking?.terms?.[term]?.cards);
    const placedOn = (/** @type {any} */ row) => row.scheduled?.sitting_id || "";
    const seated = (id) => mine.filter((row) => placedOn(row) === id).length;
    const radio = (value, text, note, checked) => `<label><input type="radio" name="sitting" value="${e(value)}"${checked ? " checked" : ""}> ${e(text)}${note ? ` <span class="makeup-muted">${e(note)}</span>` : ""}</label>`;
    const none = radio("", booked ? "None: students book their own time on their card" : "None for now", "", booked);
    form.querySelector("[data-makeup-sittings]").innerHTML = `${booked ? none : ""}
      ${sittings.map((s, i) => radio(s.id, sittingText(s), seated(s.id) ? `· ${plural(seated(s.id), "student")} ${booked ? "booked" : "on it"}` : "", !booked && i === 0)).join("")}
      ${radio("new", "A new makeup sitting", "", !booked && !sittings.length)}${booked ? "" : none}
      <div class="makeup-new" data-makeup-new${booked || sittings.length ? " hidden" : ""}><label class="field">Name<input name="label" maxlength="280" value="Makeup"></label>
        <div class="absence-dates"><label class="field">Date<input type="date" name="date"></label><label class="field">Time <span class="field-optional">optional</span><input name="time" maxlength="280"></label></div>
        <label class="field">Where <span class="field-optional">optional</span><input name="location" maxlength="280"></label></div>`;
    // KEEP THEIR SITTING (requirement, 2026-10-06): a student already on a makeup sitting starts unticked, and their row says where they
    // are, so approving moves them only when you tick them. Each row is the name, the section and one short status (2026-11-01).
    form.querySelector("[data-makeup-students]").innerHTML = mine.length
      ? `<p class="field-hint" data-students-hint></p><div class="makeup-students">${mine.map((row, i) => `<label class="makeup-student"><input type="checkbox" data-approve-student="${i}"${!booked && row.state === "owed" && !placedOn(row) ? " checked" : ""}>
          <span>${e(row.name || "Student")}</span><span class="makeup-muted">Section ${e(row.section)}</span><span class="makeup-student-status" data-approve-where="${i}"></span></label>`).join("")}</div>`
      : '<p class="field-hint">No student owes a makeup of this assignment now. Place students on the sitting later from the Absences page or the Exams page.</p>';
    const owedCount = mine.filter((row) => row.state === "owed").length;
    // NO COPIES TO ASK FOR (GRADING "MAKEUP VERSIONS", requirement, 2026-11-05): with booking cards on, each booked student's copy prints by
    // itself, and a spare is printed from Print copies or Print more copies…; the field shows only without cards.
    form.elements.copies.value = booked ? "0" : String(owedCount || 1);
    form.querySelector("[data-copies-field]").hidden = booked;
    const summarize = () => {
      const chosen = form.querySelector("[name=sitting]:checked")?.value || "";
      const sitting = chosen === "new" ? { new: true, date: form.elements.date.value } : chosen ? sittings.find((s) => s.id === chosen) || {} : null;
      const picking = Boolean(sitting) || !booked;
      form.querySelector("[data-makeup-new]").hidden = chosen !== "new";
      form.querySelector("[data-students-legend]").textContent = picking ? `Place on ${chosen === "new" ? "the new sitting" : chosen ? "this sitting" : "a sitting"}` : "Students who owe this makeup";
      form.querySelector(".makeup-students")?.classList.toggle("is-reading", !picking);
      const hint = form.querySelector("[data-students-hint]");
      if (hint) hint.textContent = !picking ? "Nothing to tick: each student picks a time on their booking card. Approving prints a named copy for each student already booked; later ones print when they book."
        : booked ? "Tick a student to place them on this sitting yourself." : chosen ? "Ticked students are placed on this sitting." : "Choose a sitting above to place students on it.";
      form.querySelectorAll("[data-approve-student]").forEach((box) => {
        const row = mine[Number(box.dataset.approveStudent)];
        const here = Boolean(chosen) && placedOn(row) === chosen;
        // A student already on the chosen sitting shows ticked and fixed; the tick goes again with another choice.
        box.disabled = here;
        if (here) { box.checked = true; box.dataset.here = "1"; } else if (box.dataset.here) { box.checked = false; delete box.dataset.here; }
        form.querySelector(`[data-approve-where="${box.dataset.approveStudent}"]`).textContent = studentStatus(row, chosen, picking && box.checked);
      });
      const ticked = picking ? [...form.querySelectorAll("[data-approve-student]:checked:not(:disabled)")].map((box) => mine[Number(/** @type {HTMLElement} */ (box).dataset.approveStudent)]) : [];
      form.querySelector("[data-approve-summary]").textContent = approveSummary({ letter: draft.makeup.letter, title: options.title,
        copies: Math.max(0, Number(form.elements.copies.value) || 0), sitting, students: ticked.length, booked,
        moving: chosen ? ticked.filter((row) => placedOn(row) && placedOn(row) !== chosen).length : 0, keys: keyCounts(revision.result) });
    };
    form.addEventListener("change", summarize);
    form.addEventListener("input", summarize);
    summarize();
    form.onsubmit = (event) => {
      event.preventDefault();
      void runFormAction(form, async () => {
        const chosen = form.querySelector("[name=sitting]:checked")?.value || "";
        const sitting = chosen === "new" ? { label: form.elements.label.value, date: form.elements.date.value, time: form.elements.time.value, location: form.elements.location.value }
          : chosen ? { id: chosen } : null;
        if (chosen === "new" && !form.elements.date.value) throw new Error("Choose the makeup sitting's date.");
        // ON THEIR CARDS (requirement, 2026-10-15): with booking cards on and no sitting chosen, a ticked student stays on their own
        // booking card (sent or waiting on their answer); approving places no one, and the window no longer asks for a sitting.
        const students = sitting || !booked ? [...form.querySelectorAll("[data-approve-student]:checked:not(:disabled)")].map((box) => mine[Number(box.dataset.approveStudent)]).map((row) => [row.section_id, row.enrollment_id]) : [];
        if (students.length && !sitting) throw new Error("Choose the makeup sitting to place the students on, or untick them.");
        await api(`${draftBase(draft.id)}/approve`, "POST", { request_id: crypto.randomUUID(), confirmation: "approve", revision_id: revision.id,
          copies: Number(form.elements.copies.value) || 0, sitting, students, ...(options.certified ? { certified_ok: true } : {}),
          // This window showed each problem's answer key, so this approval is the instructor's save of them (2026-10-11).
          save_keys: true });
        toast(`Approved makeup Version ${draft.makeup.letter}`);
        await load();
      });
    };
  }

  await load();
  // Leaving the window stops the polling (the dialog is reused by the next window).
  dialog()?.addEventListener("close", () => { clearTimeout(timer); shown?.disconnect(); }, { once: true });
}
