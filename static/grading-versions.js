// Assignment versions: an exam's A and B behind one label sheet, a quiz's or any assignment's versions. Pure progress per version for the
// setup guide, and the panel where the instructor says which version imported questions describe and
// decides the papers whose seat and pages disagree. Nothing here switches a paper's version by itself.

import {versionProgress, versionLine, unversioned} from "./grading-guide.js";
import { viewLink } from "./file-viewer.js";

// The paper being decided, page by page (requirement, 2026-09-25).
// Images are cut on request from the scan's retained pages; each opens at full size.
const pageImage = page => `/api/documents/messages/${encodeURIComponent(page.message_id)}/pages/${encodeURIComponent(page.scan_page_id)}/image.png`;
function pagesMarkup(paper, h, first = false) {
  const pages = first ? (paper.pages || []).slice(0, 1) : paper.pages || [];
  if (!pages.length) return `<p class="field-hint">This paper's pages could not be listed. Open it in Exams.</p>`;
  return `<div class="papers-check-sides">${pages.map(page => `<figure class="papers-check-side"><a ${viewLink(`${pageImage(page)}?width=1700`, `Page ${page.page_number}`, h.e)}><img src="${h.e(pageImage(page))}" alt="Page ${h.e(page.page_number)} of this paper" loading="lazy"></a><figcaption>Page ${h.e(page.page_number)}${first && paper.pages.length > 1 ? ` of ${h.e(paper.pages.length)}` : ""}</figcaption></figure>`).join("")}</div>${first ? "" : `<p class="field-hint">Open a page to see it at full size.</p>`}`;
}

function wide() {
  const dialog = document.querySelector("#dialog");
  dialog?.classList.add("dialog-wide");
  dialog?.addEventListener("close", () => dialog.classList.remove("dialog-wide"), {once: true});
}

// One row per version (2026-09-26 overhaul): its template and how far its questions are, from versionProgress. What a version still
// lacks is said once under the table, in one line for every version (the table's short cells say which; the line says what).
// A template's label often starts with its version ("Version A · uploaded template 1"); the row already says the version.
const blankName = v => v.blank.startsWith(`Version ${v.letter} · `) ? v.blank.slice(`Version ${v.letter} · `.length) : v.blank;
function versionTable(progress, h) {
  const of = (count, total) => `<td class="${count < total ? "is-short" : ""}">${h.e(count.toLocaleString())} of ${h.e(total.toLocaleString())}</td>`;
  return `<div class="grading-version-table-wrap"><table class="grading-version-table"><caption class="sr-only">Assignment versions</caption><thead><tr><th scope="col">Version</th><th scope="col">Template</th><th scope="col">Rubrics</th><th scope="col">Areas</th><th scope="col">Approved</th><th scope="col">Key</th></tr></thead><tbody>${progress.map(v => `<tr data-version-row="${h.e(v.letter)}"><th scope="row">${h.e(v.letter)}</th><td class="${v.blank ? "" : "is-short"}">${v.blank ? h.e(blankName(v)) : "None yet"}</td>${of(v.rubrics, v.questions)}${of(v.located, v.rubrics)}${of(v.approved, v.rubrics)}${of(v.keyed, v.rubrics)}</tr>`).join("")}</tbody></table></div>`;
}

// `onConfirmed(count)` hears how many papers' versions are confirmed, for the step's summary.
export function mountVersions(host, plan, h, refresh, active, onConfirmed = null) {
  const progress = versionProgress(plan), loose = unversioned(plan);
  if (!progress.length) { host.replaceChildren(); return; }
  // A makeup or another version no seating chart handed out (2026-09-25), added before its rubrics exist so its blank template can be
  // chosen and its rubrics drafted with the others. The server records it; a paper's version is still decided as before.
  const letters = plan.exam_versions?.letters || [], last = letters.at(-1) || "";
  const next = last && last < "Z" ? String.fromCharCode(last.charCodeAt(0) + 1) : "";
  const removable = (plan.exam_versions?.added || []).filter(letter => !plan.questions.some(q => q.exam_version === letter));
  const adding = `<div class="grading-actions" data-version-add>${next ? h.button("add-exam-version", `Add Version ${next}`) : ""}${removable.map(letter => h.button("remove-exam-version", `Remove Version ${letter}`, `data-letter="${h.e(letter)}"`)).join("")}</div><p class="supporting-copy">Add a makeup or another version no seating chart handed out. It gets its own blank template and rubrics; its papers’ versions are confirmed as before.</p>`;
  host.innerHTML = `<section class="grading-panel" data-exam-versions><h3>Assignment versions</h3><p class="section-lead">Each version needs its rubrics and blank template; papers whose version is unclear wait below.</p>${versionTable(progress, h)}${progress.some(v => v.missing.length) ? `<p class="grading-warning" data-version-missing>${progress.filter(v => v.missing.length).map(v => h.e(versionLine(v))).join(" ")}</p>` : ""}${adding}${loose.length ? `<p class="grading-warning">${h.e(loose.length)} question${loose.length === 1 ? " was" : "s were"} imported before versions existed. Say which version ${loose.length === 1 ? "it describes" : "they describe"} before uploading another version’s rubrics.</p><div class="grading-actions">${h.button("assign-version", "Say which version these describe")}</div>` : ""}<div data-version-papers><p class="supporting-copy">Checking the papers…</p></div></section>`;
  host.querySelector('[data-grading-action="add-exam-version"]')?.addEventListener("click", event => h.run(event.currentTarget, async () => {
    await h.api(`/api/grading/assignments/${plan.id}/exam-versions/add`, "POST", {version: plan.version, exam_version: next});
    h.toast(`Version ${next} added. Choose its blank template under Assignment Templates.`);
    if (active()) await refresh();
  }));
  host.querySelectorAll('[data-grading-action="remove-exam-version"]').forEach(button => button.addEventListener("click", event => h.run(event.currentTarget, async () => {
    await h.api(`/api/grading/assignments/${plan.id}/exam-versions/remove`, "POST", {version: plan.version, exam_version: button.dataset.letter});
    h.toast(`Version ${button.dataset.letter} removed.`);
    if (active()) await refresh();
  })));
  host.querySelector('[data-grading-action="assign-version"]')?.addEventListener("click", () => {
    h.dialog("Which version do these questions describe?", "This is recorded once. Rubrics, approvals, locations and keys stay as they are.", `<ul>${loose.map(q => `<li>${h.e(q.content.title)}</li>`).join("")}</ul>${h.select("Assignment version", "letter", [["", "Choose the version"], ...progress.map(v => [v.letter, `Version ${v.letter}`])])}`, "Record version", async (values, _, live) => {
      if (!values.get("letter")) throw new Error("Choose the version.");
      await h.api(`/api/grading/assignments/${plan.id}/exam-version`, "POST", {version: plan.version, exam_version: values.get("letter"), question_ids: loose.map(q => q.question_id)});
      if (live()) { h.close(); await refresh(); }
    });
  });
  void papers();
  async function papers() {
    const slot = host.querySelector("[data-version-papers]");
    try {
      const data = await h.api(`/api/grading/assignments/${plan.id}/paper-versions`);
      if (!active() || !slot.isConnected) return;
      const c = data.counts, n = key => c[key] || 0;
      onConfirmed?.(n("confirmed") + n("instructor"));
      const summary = [`${n("confirmed") + n("instructor")} papers confirmed`, n("checking") ? `${n("checking")} being compared with the blank assignments` : "",
        n("waiting_blank") ? `${n("waiting_blank")} waiting for their version’s blank assignment` : "", data.waiting.length ? `${data.waiting.length} need your decision` : ""].filter(Boolean).join(" · ");
      slot.innerHTML = `<p>${h.e(summary)}</p>${data.waiting.length ? `<details open><summary>Papers that need your decision</summary>${data.waiting.some(p => p.matched) ? `<p>${h.button("decide-proposed", `Confirm proposed versions (${data.waiting.filter(p => p.matched).length})`)} <small>Papers with no seat, such as DR-center and makeup exams: each one you tick is recorded as your decision.</small></p>` : ""}<ul class="grading-task-list">${data.waiting.map((p, i) => `<li><span><strong>${h.e(p.name || `Paper ${i + 1}`)}</strong></span><p>${h.e(p.reason)}</p>${p.scores ? `<small>Match with each blank assignment: ${h.e(Object.entries(p.scores).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(" · "))}</small>` : ""}${h.button("decide-version", "Decide", `data-index="${i}"`)}</li>`).join("")}</ul></details>` : ""}`;
      // Several papers at once (2026-09-24: DR-center and makeup exams have no seat to confirm their version). Each paper is listed
      // with the version its pages matched and is recorded only if the instructor ticks it; nothing is preselected.
      slot.querySelector('[data-grading-action="decide-proposed"]')?.addEventListener("click", () => {
        const proposed = data.waiting.map((paper, index) => ({paper, index})).filter(({paper}) => paper.matched);
        h.dialog("Confirm the proposed versions", "Each paper's pages were compared with every version's blank assignment. Tick the papers you looked at whose version is the one proposed.",
          `<p><a href="#exams/${h.e(plan.event_id)}" target="_blank" rel="noopener">Open the collected papers in Exams</a></p><ul class="grading-task-list">${proposed.map(({paper, index}) =>
            `<li>${h.check(`${paper.name || `Paper ${index + 1}`}: Version ${paper.matched}${paper.scores ? ` (${Object.entries(paper.scores).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(" · ")})` : ""}`, `paper-${index}`)}<p>${h.e(paper.reason)}</p>${pagesMarkup(paper, h, true)}</li>`).join("")}</ul>`,
          "Record ticked versions", async (values, _, live) => {
            const ticked = proposed.filter(({index}) => values.has(`paper-${index}`));
            if (!ticked.length) throw new Error("Tick the papers you looked at.");
            for (const {paper} of ticked) {
              await h.api(`/api/grading/assignments/${plan.id}/paper-versions/confirm`, "POST", {section_id: paper.section_id, enrollment_id: paper.enrollment_id,
                assembly_revision_id: paper.assembly_revision_id, exam_version: paper.matched, note: "Confirmed with other proposed versions.", confirm_version: true});
            }
            // The whole step is read again: its notice, its "Decide versions" action and the graph count these papers too.
            if (live()) { h.close(); h.toast(`Recorded ${ticked.length} version${ticked.length === 1 ? "" : "s"}.`); await refresh(); }
          });
        wide();
      });
      slot.querySelectorAll('[data-grading-action="decide-version"]').forEach(button => button.onclick = () => {
        const paper = data.waiting[Number(button.dataset.index)];
        const form = h.dialog("Which version did this student write?", paper.reason, `${pagesMarkup(paper, h)}<p><a href="#exams/${h.e(plan.event_id)}" target="_blank" rel="noopener">Open the collected paper in Exams</a></p>${h.select("Assignment version", "letter", [["", "Choose the version"], ...data.graded.map(v => [v, `Version ${v}`])], paper.matched || "")}${h.area("Note (optional)", "note", "")}${h.check("I looked at this paper and it is this version", "confirmed")}<p class="supporting-copy">Only this paper is decided. The seating chart and every other paper stay as they are.</p>`, "Record this paper’s version", async (values, _, live) => {
          if (!values.get("letter") || !values.has("confirmed")) throw new Error("Choose the version and confirm that you looked at the paper.");
          await h.api(`/api/grading/assignments/${plan.id}/paper-versions/confirm`, "POST", {section_id: paper.section_id, enrollment_id: paper.enrollment_id,
            assembly_revision_id: paper.assembly_revision_id, exam_version: values.get("letter"), note: values.get("note"), confirm_version: true});
          if (live()) { h.close(); h.toast("Recorded this paper’s version."); await refresh(); }
        });
        // The confirmation tick sits beside the button it unlocks, above the refusal it would cause (as the answer-area dialog's).
        const tick = form.querySelector('[name="confirmed"]').closest("label");
        tick.classList.add("quality-confirm-tick");
        form.querySelector(".dialog-footer").prepend(form.querySelector(".form-error"), tick);
        wide();
      });
    } catch (error) { if (active() && slot.isConnected) slot.innerHTML = `<p role="alert">${h.e(error.message)}</p>`; }
  }
}
