// Creating the assignment on Gradescope (AGENTS.md "Sending grades to Gradescope"; requirement, 2026-09-27). Part of the Gradescope panel
// of the Certification and Export step. The server decides everything: its preview says what Faculty would make in which Gradescope
// courses (each version with its blank, its questions and boxes, its rubric lines) and why anything would not be made; Create names
// that preview's fingerprint; the background sender makes it a step at a time. This page shows, asks, and never creates by itself.
import {runFormAction} from "./ui.js";

// A preview reads every chosen Gradescope course: far longer than the default request wait.
const WAIT = 180000;
const count = (n, one, many = `${one}s`) => `${Number(n || 0).toLocaleString()} ${Number(n) === 1 ? one : many}`;
const createBase = plan => `/api/grading/gradescope/assignments/${encodeURIComponent(plan.id)}`;
const versionNames = letters => `${letters.length === 1 ? "Version" : "Versions"} ${letters.join(", ")}`;

export const creationLive = creation => ["pending", "running"].includes(creation?.status);
export const creationUnitWords = {pending: "waiting", sending: "working", uncertain: "checking Gradescope", complete: "made", failed: "stopped", cancelled: "cancelled"};

// The makeup versions Faculty could not add by itself (GRADESCOPE "A VERSION THE SET LACKS": one attempt each, the reason recorded once;
// `status.automatic.versions_not_added`): one line each, with Create the assignment on Gradescope… as the way to add it. Read only.
export function notAddedMarkup(automatic, h) {
  const versions = automatic?.versions_not_added || [];
  if (!versions.length) return "";
  return `<ul class="grading-gradescope-notes" data-gradescope-not-added>${versions.map(v => `<li class="grading-warning">Faculty could not add makeup Version ${h.e(v.letter)} by itself: ${h.e((v.reasons || [])[0] || "no reason recorded")}${(v.reasons || []).length > 1 ? ` (and ${h.e(v.reasons.length - 1)} more)` : ""}. Create the assignment on Gradescope… adds it.</li>`).join("")}</ul>`;
}

// What the latest creation is doing, per Gradescope course: the step it is on, or why it stopped. `automatic`: the status's
// `automatic` (makeup versions Faculty could not add are listed on the card, or alone when nothing was made).
export function creationsMarkup(creations, h, configured = true, automatic = null) {
  const latest = (creations || [])[0];
  const notAdded = notAddedMarkup(automatic, h);
  if (!latest) return notAdded ? `<article class="grading-gradescope-send" data-gradescope-creation-notes aria-label="Makeup versions on Gradescope"><header><span class="grading-batch-caption">Makeup versions on Gradescope</span><h4>Not added</h4></header>${notAdded}</article>` : "";
  const units = latest.units || [];
  const stopped = units.filter(u => u.status === "failed").length;
  const heading = latest.status === "cancelled" ? "Cancelled" : stopped ? `Stopped in ${count(stopped, "course")}` : creationLive(latest) ? "Making it" : "Made";
  const when = latest.created_at ? new Date(latest.created_at).toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"}) : "";
  const actions = [creationLive(latest) ? h.button("gradescope-create-cancel", "Cancel") : "",
    stopped && latest.status !== "cancelled" && configured ? h.button("gradescope-create-retry", `Retry (${stopped})`) : ""].filter(Boolean).join("");
  return `<article class="grading-gradescope-send" data-gradescope-creation="${h.e(latest.id)}" aria-label="${h.e(`Making ${latest.title} on Gradescope${when ? `, ${when}` : ""}`)}">
    <header><span class="grading-batch-caption">Making “${h.e(latest.title)}” on Gradescope${when ? ` · ${h.e(when)}` : ""}</span><h4>${h.e(heading)}</h4></header>
    ${notAdded}
    ${latest.automatic?.after === "certified" ? '<p class="supporting-copy" data-gradescope-automatic>Made by Faculty by itself, after you certified.</p>'
      : latest.automatic ? `<p class="supporting-copy" data-gradescope-automatic>Faculty added makeup Version ${h.e(latest.automatic.letter)} by itself once it was ready to grade${(latest.automatic.skipped || []).length ? `; not to ${latest.automatic.skipped.map(s => `Section ${h.e(s.section)} (${h.e(s.reason)})`).join(", ")}` : ""}.</p>` : ""}
    <ul class="grading-gradescope-links">${units.map(u => `<li><strong>${h.e(u.course || u.course_id)}</strong>${u.sheets ? ` <span class="supporting-copy" data-gradescope-creation-sheets>with “${h.e(u.sheets)}”</span>` : ""} <span data-gradescope-state="${h.e(u.status)}">${h.e(creationUnitWords[u.status] || u.status)}${u.step && u.status !== "failed" ? ` · ${h.e(u.step)}` : ""}</span>${u.error ? `<p class="${u.status === "failed" ? "grading-warning" : "supporting-copy"}" role="status">${h.e(u.error)}</p>` : ""}</li>`).join("")}</ul>
    ${stopped ? '<p class="supporting-copy">Faculty stops rather than guess when Gradescope shows something it did not make. Fix it there (or remove what is in the way), then Retry.</p>' : ""}
    ${!creationLive(latest) && !stopped && latest.status !== "cancelled" ? `<p class="supporting-copy">Made. Send to Gradescope (or Upload papers only, which sets no grade) now proposes it for these sections${units.some(u => u.sheets) ? "; the Bubble sheets page sends the sheets to the bubble-sheet exam" : ""}.</p>` : ""}
    ${actions ? `<div class="grading-actions">${actions}</div>` : ""}
  </article>`;
}

// The preview: what would be made, where, and what stops it.
export function createPreviewMarkup(preview, h) {
  const versions = preview.versions || [], courses = preview.courses || [];
  const leftOut = (preview.sections || []).filter(s => s.reason);
  const problems = preview.problems || [], warnings = preview.warnings || [];
  const made = courses.filter(c => !c.problem);
  // A course whose section already goes to a version set that lacks a version its papers are in gets that version added (2026-10-06).
  const adding = made.filter(c => c.adds), creating = made.filter(c => !c.adds);
  const parts = [];
  if (preview.running) parts.push('<p class="grading-warning" role="status">Faculty is still making this assignment on Gradescope. Let it finish, or cancel it, before creating again.</p>');
  if (problems.length) parts.push(`<div class="callout grading-warning" data-tone="warning" role="status" data-gradescope-create-problems><p><strong>Before Faculty can make it:</strong></p><ul>${problems.map(p => `<li>${h.e(p)}</li>`).join("")}</ul></div>`);
  if (versions.length) parts.push(`<h4 class="grading-gradescope-heading">What Faculty makes${made.length > 1 ? " in each course" : ""}</h4><ul class="grading-gradescope-links" data-gradescope-create-versions>${versions.map(v =>
    `<li><strong>${h.e(v.title)}</strong> <span>${h.e(count(v.pages, "page"))} (its blank) · ${h.e(count(v.questions, "question"))} with their answer boxes · ${h.e(count(v.lines, "rubric line"))}</span></li>`).join("")}</ul>${versions.length > 1 && (creating.length || !adding.length) ? `<p class="supporting-copy">The versions are grouped as one assignment named “${h.e(preview.title)}”, as Organize Exam Versions does.</p>` : ""}`);
  parts.push(`<h4 class="grading-gradescope-heading">Where</h4>${courses.length ? `<ul class="grading-gradescope-links" data-gradescope-create-courses>${courses.map(c =>
    `<li><strong>${h.e(c.course)}</strong> <span>${c.sections?.length ? `${c.sections.length === 1 ? "Section" : "Sections"} ${c.sections.map(h.e).join(", ")}` : ""}${c.adds ? ` · adds ${h.e(versionNames(c.adds.letters))} to “${h.e(c.adds.container)}”${c.adds.has?.length ? ` (it has ${h.e(c.adds.has.join(", "))})` : ""}` : ""}</span>${c.problem ? `<p class="grading-warning" role="status">${h.e(c.problem)}</p>` : ""}</li>`).join("")}</ul>` : `<p class="grading-gradescope-empty">No section needs a new ${preview.sheets ? "written " : ""}assignment or version: each already goes to one on Gradescope with every version its papers are in, or has no Gradescope course chosen yet.</p>`}`);
  const sheets = preview.sheets, sheetCourses = (sheets?.courses || []).filter(c => !c.problem);
  if (sheets) parts.push(sheetsMarkup(sheets, h));
  if (leftOut.length) parts.push(`<details class="disclosure grading-gradescope-list"><summary>Sections not included · ${h.e(leftOut.length)}</summary><ul class="grading-gradescope-notes">${leftOut.map(s => `<li>Section ${h.e(s.section)}: ${h.e(s.reason)}</li>`).join("")}</ul></details>`);
  if (warnings.length) parts.push(`<details class="disclosure grading-gradescope-list" data-gradescope-warnings><summary>Warnings · ${h.e(warnings.length)}</summary><ul class="grading-gradescope-notes">${warnings.map(w => `<li>${h.e(w)}</li>`).join("")}</ul></details>`);
  const added = [...new Set(adding.flatMap(c => c.adds.letters || []))].sort();
  // What the written assignment lacks holds back only it (THE BUBBLE-SHEET EXAM TOO): the tick then names the bubble sheets alone.
  const written = !problems.length;
  const doing = [written && creating.length ? `makes “${preview.title}” in ${count(creating.length, "Gradescope course")}` : "",
    written && adding.length ? `adds ${versionNames(added)} to ${adding.length === 1 ? `“${adding[0].adds.container}”` : "the sections' assignments"} in ${count(adding.length, "Gradescope course")}` : "",
    sheets && !sheets.problems?.length && sheetCourses.length ? `makes the bubble-sheet exam “${sheets.title}” with its answer key in ${count(sheetCourses.length, "Gradescope course")}` : ""].filter(Boolean).join(" and ");
  if (preview.ready) parts.push(`<div class="grading-gradescope-confirm">${h.check(`I checked this. Faculty ${doing}; nothing is published and no student is emailed.`, "understood")}</div>`);
  return parts.join("");
}

// The bubble-sheet exam Faculty makes beside the written one (GRADESCOPE "THE BUBBLE-SHEET EXAM TOO"): its versions with the
// answer key's questions and points, where, and what stops it. Its problems hold back only this part.
export function sheetsMarkup(sheets, h) {
  const courses = sheets.courses || [], problems = sheets.problems || [];
  const parts = [`<h4 class="grading-gradescope-heading">Bubble-sheet exam</h4>`];
  if (problems.length) parts.push(`<div class="callout grading-warning" data-tone="warning" role="status" data-gradescope-create-sheet-problems><p><strong>Before Faculty can make the bubble-sheet exam:</strong></p><ul>${problems.map(p => `<li>${h.e(p)}</li>`).join("")}</ul></div>`);
  if (!problems.length) parts.push(`<ul class="grading-gradescope-links" data-gradescope-create-sheet-versions>${(sheets.versions || []).map(v =>
    `<li><strong>${h.e(v.title)}</strong> <span>Gradescope's bubble sheet · ${h.e(count(v.questions, "question"))} in the answer key · ${h.e(v.points)} points</span></li>`).join("")}</ul><p class="supporting-copy">Grouped as one bubble-sheet exam named “${h.e(sheets.title)}”, with Faculty's answer key. Gradescope scores the sheets with it; Faculty's own scores stay the grade.</p>`);
  parts.push(courses.length ? `<ul class="grading-gradescope-links" data-gradescope-create-sheet-courses>${courses.map(c =>
    `<li><strong>${h.e(c.course)}</strong> <span>${c.sections?.length ? `${c.sections.length === 1 ? "Section" : "Sections"} ${c.sections.map(h.e).join(", ")}` : ""}</span>${c.problem ? `<p class="grading-warning" role="status">${h.e(c.problem)}</p>` : ""}</li>`).join("")}</ul>`
    : '<p class="grading-gradescope-empty">No section needs a bubble-sheet exam: each already goes to one on Gradescope, or has no Gradescope course chosen yet.</p>');
  if (sheets.linked?.length) parts.push(`<p class="supporting-copy">Already linked to a bubble-sheet exam: ${sheets.linked.length === 1 ? "Section" : "Sections"} ${sheets.linked.map(h.e).join(", ")}.</p>`);
  return `<section data-gradescope-create-sheets>${parts.join("")}</section>`;
}

// `targets` are the course and assignment choices the send dialog had, so the preview covers the same sections.
export function createDialog(plan, h, onCreated, targets = {}) {
  let preview = null, asked = null, requestId = "", generation = 0, loading = false, creating = false;
  const form = h.dialog("Create the assignment on Gradescope", "Faculty reads Gradescope and shows what it would make. Nothing is made until you tick the box and create.",
    `${h.field("Name on Gradescope", "title", plan.title || "", "text", 'maxlength="100" required')}
     <p class="supporting-copy">For every section whose Gradescope course has no assignment for this yet. With versions, each is named “Version A”, “Version B” … after this name and grouped under it. A section whose papers are in a version its Gradescope assignment lacks gets that version added to it.</p>
     <div data-gradescope-create-sheet-name hidden>${h.field("Name of the bubble-sheet exam", "sheet_title", "", "text", 'maxlength="100"')}</div>
     <div data-gradescope-create-outlook aria-live="polite"></div>`,
    "Create on Gradescope", async () => create());
  form.classList.add("grading-batch-form");
  const active = () => form.isConnected && form.closest("dialog")?.open;
  const outlook = form.querySelector("[data-gradescope-create-outlook]"), error = form.querySelector(".form-error");
  const sheetName = form.querySelector("[data-gradescope-create-sheet-name]");
  // The second name as the preview used it: the server's default ("<title> - Multiple Choice") until the instructor edits it.
  let sheetEdited = false;
  const sheetAsked = () => sheetEdited ? form.elements.sheet_title.value : undefined;
  const submit = form.querySelector('button[type="submit"]');
  const blocked = () => creating ? "Creating…" : asked && (asked.title !== form.elements.title.value || asked.sheet_title !== sheetAsked()) ? "Finish editing the name to update the preview." : loading || !preview ? "Checking Gradescope…" : !preview.configured ? "Gradescope is not set up."
    : !preview.ready ? "There is nothing Faculty can make yet." : "";
  const controls = () => { const reason = blocked(); submit.disabled = Boolean(reason); submit.title = reason; form.elements.title.disabled = creating; form.elements.sheet_title.disabled = creating; };

  async function load(notice = "") {
    const token = ++generation;
    asked = {title: form.elements.title.value, targets: JSON.parse(JSON.stringify(targets)), ...(sheetEdited ? {sheet_title: sheetAsked()} : {})};
    preview = null; loading = true; requestId = "";
    error.hidden = true; error.textContent = "";
    outlook.innerHTML = `${notice}<p class="loading-state" role="status">Reading Gradescope and Faculty…</p>`;
    controls();
    try {
      const result = await h.api(`${createBase(plan)}/create/preview`, "POST", asked, {}, WAIT);
      if (!active() || token !== generation) return;
      preview = result; requestId = crypto.randomUUID();
      sheetName.hidden = !result.sheets;
      if (result.sheets && !sheetEdited) form.elements.sheet_title.value = result.sheets.title;
      outlook.innerHTML = notice + (result.configured ? createPreviewMarkup(result, h)
        : `<div class="grading-warning" role="status"><p><strong>Gradescope is not set up for this Faculty.</strong> ${h.e(result.problem || "")}</p></div>`);
    } catch (cause) {
      if (active() && token === generation) outlook.innerHTML = `${notice}<p role="alert">${h.e(cause.message)}</p><p class="supporting-copy">Nothing was made. Change the name, or close and open this again to read Gradescope once more.</p>`;
    } finally {
      if (token === generation) { loading = false; if (active()) controls(); }
    }
  }

  async function create() {
    if (blocked()) throw new Error(blocked());
    if (!form.elements.understood?.checked) throw new Error("Tick the box to confirm you checked what Faculty will make.");
    creating = true;
    controls();
    let receipt;
    try {
      // The same request ID for the same preview: pressing Create again after an unanswered request returns what the server recorded.
      receipt = await h.api(`${createBase(plan)}/create`, "POST", {request_id: requestId, confirmation: "create", fingerprint: preview.fingerprint, ...asked}, {}, WAIT);
    } catch (cause) {
      const status = Number(cause.status || 0);
      if (status >= 400 && status < 500) {
        if (active()) void load(`<p class="grading-warning" role="status">${h.e(cause.message)} Nothing was made. Check the new preview.</p>`);
        return;
      }
      throw new Error("Faculty could not confirm whether this was recorded. Create again asks with the same request: it returns what was recorded, or records it once.");
    } finally {
      creating = false;
      if (active()) controls();
    }
    if (active()) h.close();
    h.toast(receipt?.status === "cancelled" ? "That was already recorded and cancelled." : "Faculty is making the assignment on Gradescope. Follow it on this step; then send as usual.");
    onCreated?.();
  }

  form.onsubmit = event => { event.preventDefault(); void runFormAction(form, create).then(() => { if (active()) controls(); }); };
  // A pause in typing reads the preview again too: a name edited back to what it was fires no "change", and Create would wait.
  let typing = 0;
  const edited = () => {
    ++generation;
    preview = null; requestId = ""; loading = false;
    outlook.innerHTML = '<p class="supporting-copy" role="status">Finish editing the name to update the preview.</p>';
    controls();
    clearTimeout(typing);
    typing = window.setTimeout(() => { if (active()) void load(); }, 800);
  };
  form.elements.title.addEventListener("input", edited);
  form.elements.sheet_title.addEventListener("input", () => { sheetEdited = true; edited(); });
  for (const name of ["title", "sheet_title"]) form.elements[name].addEventListener("change", () => { clearTimeout(typing); void load(); });
  void load();
  return form;
}
