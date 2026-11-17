import {helpers} from "./grading-ui.js";
import {stageHref} from "./scan-flow.js";

// These dialogs organize a retained scan. Page identities and source-review
// decisions continue through their existing server-validated tools.
export function bindScanGrading(host, item, options, refresh) {
  const h = helpers(options), message = item.message_id;
  host.querySelectorAll("[data-scan-grading]").forEach(button => {
    button.onclick = () => h.run(button, async () => {
      const state = await h.api(`/api/grading/scans/${message}`);
      if (!host.isConnected) return;
      if (button.dataset.scanGrading === "review") {
        if (!state.plan) throw new Error("Choose an assignment first.");
        const scope = `${state.plan.title} · ${state.plan.code} · ${state.plan.term === "Other" ? state.plan.term_other : state.plan.term} ${state.plan.year}`;
        // Only the steps for the whole scan hold its review back (2026-09-28); every one of them, not only the first.
        const holding = state.review_blockers;
        // The Review page disables Finish while its overview says the scan is held; this answers a click made before a refresh showed
        // that. Each blocker links to the stage that settles it (the stage whose words carry it), as on the page (2026-10-18).
        if (holding.length) {
          const stages = item.overview?.stages || [];
          const where = text => stages.find(stage => stage.key !== "review" && stage.detail && stage.detail.includes(text));
          const href = stage => options.jobId ? stageHref(options.jobId, stage) : "";
          options.showDialog(`<form>${options.dialogHeader("Finish the scan checks first", h.e(scope))}<div class="dialog-body"><div class="grading-warning"><strong>Needs you</strong><ul data-scan-blockers>${holding.map(text => { const stage = where(text); return `<li>${stage && href(stage) ? `<a href="${h.e(href(stage))}" data-action="close">${h.e(stage.label)}</a>: ` : ""}${h.e(text)}</li>`; }).join("")}</ul></div><p>Once they are done the review passes by itself, or finish it here.</p></div><footer class="dialog-footer"><button type="button" class="button primary" data-action="close">Close</button></footer></form>`);
          return;
        }
        const flagged = state.blockers || [];
        h.dialog("Finish scan review", scope,
          `<p>${h.e(state.pages)} scanned pages will stay with this assessment. Papers that are ready become reviewed papers and go on to grading.</p>${flagged.length ? `<div class="grading-warning"><strong>Stay in the Inbox for you</strong><ul data-scan-flagged>${flagged.map(text => `<li>${h.e(text)}</li>`).join("")}</ul><p>They join grading once you settle them.</p></div>` : ""}${h.check("I have checked this scan and its assignment", "confirmed")}`,
          "Finish review & open grading", async (values, _, active) => {
            if (!values.has("confirmed")) throw new Error("Check the scan and confirm your review first.");
            const result = await h.api(`/api/grading/scans/${message}/review`, "POST", {fingerprint: state.fingerprint, confirm_review: true});
            if (active()) { h.close(); location.hash = result.href; }
          });
        return;
      }
      const catalog = await h.api("/api/grading/catalog");
      if (!host.isConnected) return;
      const paper = state.recognition.papers[0], plan = state.plan, run = item.unlabeled?.run;
      // What the dialog starts from: the scan's assignment, what its labels say, or the assignment its first pages were read for.
      // Nothing else: with no suggestion the choice starts empty and is required (the first in sort order was preselected before).
      const suggested = plan?.source_assignment_id || paper?.assignment_id || run?.assignment_id || "";
      // Pages with no label that are not bubble sheets: a new assignment made from this scan finds and reads them by OCR.
      const loose = (state.recognition.without_label || 0) - (state.recognition.bubble_sheets?.sheets || 0);
      const requestId = crypto.randomUUID();
      const form = h.dialog("Choose the assignment", "Choose where these papers belong. Later scans of the same assignment and term join the same grading item.",
        `${h.select("Course and term", "term", catalog.terms.map(t => [t.id, `${t.code} · ${t.term_label}`]), plan?.term_id || paper?.term_id || run?.term_id)}<div data-assignment-choice></div><div data-new-assignment hidden>${h.field("New assignment title", "title", "", "text", 'maxlength="240"')}${h.field("Number of versions", "version_count", "0", "number", 'min="0" max="26" step="1"')}<p class="supporting-copy">How many versions are printed on the papers: 3 means Versions A, B and C. Enter 0 when every paper is the same exam.</p>${loose > 0 ? `${h.field("Scanned sides per paper", "sides", "2", "number", 'min="1" max="200" step="1"')}<p class="supporting-copy">These papers carry no labels. After saving, local OCR finds each paper’s first page and reads the name, UIN and printed version. Papers identified with confidence are registered to their students; anything unsure waits for you. Each paper’s version is the one read on its first page, when it is one of the versions above. Later the sides per paper are changed on the scan’s Papers stage.</p>` : ""}</div><fieldset><legend>Sections taking this assignment</legend><div data-scan-sections></div></fieldset><p class="supporting-copy">Existing sections stay included. Selecting more adds them to this term’s assignment record. This choice does not identify students or pages.</p>`,
        "Save assignment", async (values, _, active) => {
          const chosen = values.get("assignment");
          if (!chosen) throw new Error("Choose the assignment these papers belong to, or Create a new assignment.");
          await h.api(`/api/grading/scans/${message}/associate`, "POST", {request_id: requestId, fingerprint: state.fingerprint,
            term_id: values.get("term"), assignment_id: chosen === "new" ? null : chosen,
            new_title: chosen === "new" ? values.get("title").trim() : null, section_ids: values.getAll("section"),
            ...(chosen === "new" ? {version_count: Number(values.get("version_count")), ...(values.has("sides") ? {sides: Number(values.get("sides"))} : {})} : {})});
          if (active()) { h.close(); await refresh(); h.toast(chosen === "new" && values.has("sides") ? "Assignment saved. Local OCR is finding and reading its papers." : "Assignment saved. Papers that need nothing from you go on to grading by themselves."); }
        });
      function sections() {
        const term = form.elements.term.value, assignment = form.elements.assignment.value;
        const existing = catalog.exams.find(e => e.term_id === term && e.assignment_id === assignment);
        const selected = new Set([...(existing?.sections || []).map(s => s.id), ...state.recognition.papers.filter(p => p.term_id === term).map(p => p.section_id)]);
        const choices = catalog.sections.filter(s => s.term_id === term);
        form.querySelector("[data-scan-sections]").innerHTML = choices.map(s => `<label class="grading-check"><input type="checkbox" name="section" value="${h.e(s.id)}" ${selected.has(s.id) || choices.length === 1 ? "checked" : ""}>Section ${h.e(s.number)}</label>`).join("");
        form.querySelector("[data-new-assignment]").hidden = assignment !== "new";
        for (const name of ["title", "version_count", "sides"]) if (form.elements[name]) form.elements[name].required = assignment === "new";
      }
      function assignments() {
        const term = catalog.terms.find(t => t.id === form.elements.term.value);
        const choices = catalog.assignments.filter(a => a.definition_id === term?.definition_id);
        const preset = choices.some(a => a.id === suggested) ? suggested : "";
        form.querySelector("[data-assignment-choice]").innerHTML = h.select("Assignment", "assignment",
          [...(preset ? [] : [["", "Choose the assignment…"]]), ...choices.map(a => [a.id, a.title]), ["new", "Create a new assignment…"]], preset);
        form.elements.assignment.required = true;
        form.elements.assignment.querySelector('option[value=""]')?.setAttribute("disabled", "");
        form.elements.assignment.onchange = sections;
        sections();
      }
      form.elements.term.onchange = assignments;
      assignments();
    });
  });
}
