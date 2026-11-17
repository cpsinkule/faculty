// Bubble sheets of a retained scan: ask for a read, see what was read, and review whatever Faculty was unsure about.
// View state only. Readings are the instructor's record; nothing here grades, identifies paper or clears the review gate.
import { studentPicker } from "./student-picker.js";
import { captureDialogIntent, dialogSession, plural, whenVisible, setBusy } from "./ui.js";
import { viewLink } from "./file-viewer.js";

const STATUS = { clean: "Read", needs_review: "Needs review", reviewed: "Reviewed", repeat: "Scanned twice" };

// `stage` (the scan's Bubble sheets page, UX overhaul 2026-09-26): the page frames and names the panel and holds the one Review all
// (which sequences the one-label question and then these sheets), so the panel draws neither its own frame, heading nor Review all.
export async function mountBubbleSheets({ root, job, api, escape, toast, showDialog, dialogHeader, stage = false, sheet = null }) {
  if (job.action !== "scan.receive") return;
  const host = document.createElement("section");
  host.className = stage ? "bubble-sheets bubble-sheets-stage" : "course-panel bubble-sheets";
  // Scanned page review keeps two slots its refreshes never rewrite: bubble sheets come first in a scan of bubble sheets.
  const slot = (name) => root.querySelector(`[data-bubble-slot="${name}"]`);
  (slot("last") || root.querySelector(".inbox-job-detail")).append(host);
  let lastState = null;
  const base = `/api/bubble-sheets/messages/${encodeURIComponent(job.message_id)}`;
  let poll = null, checks = 0;
  // null until the instructor chooses: the list opens on what needs review, or on every sheet when nothing does.
  let filter = null;
  // "Review all": one sheet after another in page order until none needs review. Each review is saved as it is made, so
  // stopping loses nothing and the button picks up at the next unresolved sheet. A sheet is shown once per session, so a
  // skipped sheet, or one that still needs review after saving, cannot trap the instructor in a loop.
  let session = null;
  async function reviewNext(afterPage = 0, quiet = false) {
    const reviewing = session;
    const intended = captureDialogIntent(document.querySelector("#dialog"));
    const data = await api(base), dialog = document.querySelector("#dialog");
    if (!intended() || !reviewing || session !== reviewing || !host.isConnected) return;
    const waiting = (data.run?.status === "complete" ? data.run.sheets : []).filter((sheet) => sheet.status === "needs_review");
    const fresh = waiting.filter((sheet) => !session.visited.has(sheet.id));
    const next = fresh.find((sheet) => sheet.page > afterPage) || fresh[0];
    if (next) {
      session.visited.add(next.id);
      return openSheet(next.id, { left: waiting.length });
    }
    const finished = session;
    session = null;
    if (dialog.open) dialog.close();
    if (!quiet || finished.saved || finished.skipped) toast(waiting.length ? `${plural(finished.saved, "review")} saved. ${plural(waiting.length, "sheet")} still ${waiting.length === 1 ? "needs" : "need"} review: use Review all to go through ${waiting.length === 1 ? "it" : "them"} again.` : "Every bubble sheet in this scan is reviewed.");
    await refresh();
  }
  const reviewAll = (quiet = false) => { session = { visited: new Set(), saved: 0, skipped: 0 }; return reviewNext(0, quiet).catch((cause) => { session = null; toast(cause.message, true); }); };
  host.addEventListener("review-all", (event) => { void reviewAll(!!event.detail?.quiet); });
  host.addEventListener("scan-association-changed", () => { void refresh().catch(cause => toast(cause.message, true)); });

  function formMarkup(data) {
    const chosen = host.querySelector("form[data-bubble-request]");
    // Bubble sheets usually arrive beside their exam: start from the assignment whose labels were scanned most recently. With no
    // association, read or suggestion nothing is chosen for the instructor (2026-10-02: the first assignment alphabetically was).
    const suggested = data.assignments.some((item) => item.id === data.suggested_assignment_id) ? data.suggested_assignment_id : "";
    const assignment = data.grading?.source_assignment_id || chosen?.elements.assignment_id.value || data.run?.assignment_id || suggested || "";
    const definition = data.assignments.find((item) => item.id === assignment)?.definition_id;
    const term = data.grading?.term_id || data.sections.find((item) => item.definition_id === definition)?.term_id;
    const sections = data.sections.filter((item) => item.definition_id === definition && item.term_id === term && (!data.grading || data.grading.section_ids.includes(item.id)));
    // Keep the count typed in the form on screen (also when the assignment changes); with no form yet, show the count the last read was given by hand, never one it inferred.
    const count = chosen ? chosen.elements.question_count.value : data.run && data.run.question_source === "entered" ? data.run.question_count : "";
    // On the scan's Bubble sheets page, a next-step banner above (another stage needs the instructor first) keeps the one primary.
    const quiet = stage && Boolean(host.closest(".scan-sheets-frame")?.querySelector("[data-scan-next-step]"));
    return `<form data-bubble-request><p class="field-hint">Say which assignment these sheets are for.</p>
      ${data.grading ? `<p>Using this scan’s assignment: <strong>${escape(data.grading.title)}</strong>. Change it from the scan summary.</p>` : ""}<label class="field">Assignment<select name="assignment_id" aria-label="Bubble sheet assignment" required ${data.grading ? "disabled" : ""}><option value="" disabled ${assignment ? "" : "selected"}>Choose the assignment…</option>${data.assignments.map((item) => `<option value="${escape(item.id)}" ${item.id === assignment ? "selected" : ""}>${escape(item.code)} · ${escape(item.title)}</option>`).join("")}</select></label>
      <p class="field-hint" data-bubble-sections>${!assignment ? "Choose the assignment to see whose roster the sheets are matched against." : sections.length ? `No sections to choose: each sheet takes its section from the student it is matched to. Sheets are matched against everyone in ${escape(sections[0].term_label)} · Section${sections.length === 1 ? "" : "s"} ${sections.map((item) => item.number).join(", ")}.` : "This course has no saved sections, so there is no roster to match the sheets against."}</p>
      <label class="field">Questions on the sheet<span class="field-optional">optional</span><input name="question_count" type="number" min="1" max="100" value="${escape(String(count))}" placeholder="Automatic"></label>
      <p class="field-hint">Leave it empty to infer it from the sheets.</p>
      <details class="disclosure" data-bubble-how><summary><span>How sheets are read</span></summary><div class="disclosure-body"><p class="field-hint">A bubble sheet carries no Faculty label, so the assignment is yours to name. Students are matched by the UIN they wrote, and by their name when the UIN is unclear. An empty question count is the question most sheets have their last mark on, when at least 60% of them agree (otherwise you are asked for it). Marks past the last question are ignored. Only the template's first page (questions 1 to 100) is read.</p></div></details>
      <button class="button${data.run || quiet ? "" : " primary"}" type="submit" ${data.run ? "" : "data-bubble-first-read"} ${data.can_request ? "" : "disabled"}>${data.run ? "Read again" : "Read bubble sheets"}</button></form>`;
  }

  function runMarkup(run, { seen, readingOpen, settingsOpen }) {
    if (["pending", "running"].includes(run.status)) return `<p data-bubble-status role="status">${run.status === "pending" ? "Queued." : "Reading bubble sheets…"} The Inbox worker reads the marks, then the handwritten ID and name boxes.</p>`;
    if (run.status === "failed") return `<p role="alert">${escape(run.error)}</p>`;
    const s = run.summary, known = run.identity, percent = Math.round(run.auto_identify * 100);
    const chosen = filter ?? (run.counts.needs_review ? "needs_review" : "all");
    const shown = run.sheets.filter((sheet) => chosen === "all" || sheet.status === chosen);
    return `<div class="bubble-run-context" data-bubble-status><div><h4>${escape(run.code)} · ${escape(run.title)}</h4><p>Matched against Section${run.sections.length === 1 ? "" : "s"} ${run.sections.map(escape).join(", ")}</p></div><p class="bubble-run-size">${plural(s.sheets, "sheet")} <span aria-hidden="true">·</span> ${plural(run.question_count, "question")}${run.question_source === "inferred" && s.question_agreement ? ` <small data-question-inferred>inferred: the last mark on ${Math.round(s.question_agreement * 100)}% of sheets</small>` : ""}</p></div>
      <dl class="bubble-read-counts" aria-label="Reading results"><div data-bubble-count="clean"><dt>Read cleanly</dt><dd>${run.counts.clean}</dd></div><div data-bubble-count="needs_review" ${run.counts.needs_review ? 'class="bubble-count-attention"' : ""}><dt>Need review</dt><dd>${run.counts.needs_review}</dd></div><div data-bubble-count="reviewed"><dt>Reviewed by you</dt><dd>${run.counts.reviewed}</dd></div></dl>
      ${known.none ? `<div class="callout bubble-notice" data-tone="warning"><p>${plural(known.none, "sheet")} ${known.none === 1 ? "has" : "have"} no student identified yet.</p></div>` : ""}
      ${s.usual_last_question && s.usual_last_question !== run.question_count ? `<div class="callout bubble-notice" data-tone="warning"><p><strong>Check the question count.</strong> Most sheets have their last mark at question ${s.usual_last_question}, not ${run.question_count}.</p></div>` : ""}
      <div class="bubble-results-toolbar"><div class="bubble-result-actions">${run.counts.needs_review && !stage ? `<button class="button primary" type="button" data-bubble-review-all>Review all (${run.counts.needs_review})</button>` : ""}<a class="button" ${viewLink(`/api/bubble-sheets/runs/${encodeURIComponent(run.id)}/export.csv`, "Bubble sheet readings", escape)}>Readings (CSV)</a></div>
      <label class="field field-inline bubble-filter">Show<select data-bubble-filter aria-label="Show sheets"><option value="needs_review" ${chosen === "needs_review" ? "selected" : ""}>Needs review (${run.counts.needs_review})</option><option value="clean" ${chosen === "clean" ? "selected" : ""}>Read cleanly (${run.counts.clean})</option><option value="reviewed" ${chosen === "reviewed" ? "selected" : ""}>Reviewed (${run.counts.reviewed})</option><option value="all" ${chosen === "all" ? "selected" : ""}>All (${run.sheets.length})</option></select></label></div>
      ${shown.length ? `<div class="document-version-pages-wrap bubble-results-table" role="region" aria-label="Bubble sheet readings" tabindex="0"><table class="document-version-pages bubble-table"><thead><tr><th scope="col">Page</th><th scope="col">Student</th><th scope="col">Version</th><th scope="col">Answers</th><th scope="col">Status</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody>${shown.map((sheet) => `<tr data-status="${sheet.status}"><td>${sheet.page}</td><td>${sheet.student ? escape(sheet.student) : "<em>no student recorded</em>"}${sheet.identified_by === "confidence" ? `<br><small>identified at ${Math.round(sheet.confidence * 100)}%</small>` : sheet.match === "suggested" && !sheet.identified_by ? `<br><small>suggested, ${Math.round(sheet.confidence * 100)}%</small>` : ""}</td><td>${escape(sheet.version || "—")}</td><td><code>${escape(sheet.answers)}</code></td><td><strong>${STATUS[sheet.status]}</strong>${sheet.reasons.length ? `<br><small>${sheet.reasons.map(escape).join(" ")}</small>` : ""}</td><td><button class="text-button" type="button" data-bubble-sheet="${escape(sheet.id)}">${sheet.status === "needs_review" ? "Review" : "Open"}</button></td></tr>`).join("")}</tbody></table></div><p class="bubble-table-key">In Answers, · is a blank and (BD) is several filled bubbles.</p>` : `<div class="empty-state bubble-empty" data-kind="filtered" role="status"><h3>${chosen === "needs_review" ? "No sheets need review." : "No sheets in this view."}</h3><p>Choose All to browse the readings.</p></div>`}
      <details class="disclosure bubble-details" data-bubble-reading-details ${readingOpen ? "open" : ""}><summary>Reading details</summary><div class="bubble-detail-content"><dl class="bubble-read-facts"><div><dt>Scan coverage</dt><dd>${plural(s.sheets, "bubble sheet")} on ${plural(s.pages, "page")}</dd></div><div><dt>Last read</dt><dd>${escape(new Date(run.updated_at).toLocaleString())}</dd></div></dl>${seen}<h4>How students were identified</h4><dl class="bubble-identity-counts"><div data-bubble-identity="uin"><dt>Exact UIN</dt><dd>${known.uin}</dd></div>${known.label ? `<div data-bubble-identity="label"><dt>Scantron label</dt><dd>${known.label}</dd></div>` : ""}<div data-bubble-identity="confidence"><dt>Digits and name <span>at ${percent}% confidence or more</span></dt><dd>${known.confidence}</dd></div><div data-bubble-identity="review"><dt>By you</dt><dd>${known.review || 0}</dd></div><div data-bubble-identity="none"><dt>Not identified yet</dt><dd>${known.none}</dd></div></dl><p class="field-hint">Readings are your own record for comparison with Gradescope; nothing is graded here.</p></div></details>
      <details class="disclosure bubble-details" data-bubble-identification-settings ${settingsOpen ? "open" : ""}><summary>Identification settings</summary><div class="bubble-detail-content"><form class="bubble-threshold" data-bubble-threshold><label class="field">Identify a student automatically at<span class="bubble-threshold-input"><input name="auto_identify" type="number" min="60" max="100" step="1" value="${percent}" required aria-label="Identify a student automatically at this confidence, in percent"> % confidence or more</span></label><button class="button" type="submit">Apply</button>
      <p class="field-hint">Confidence is 55% of how close the digits read are to the UIN plus 45% of how close the name read is. Below this a student is only suggested, and an exact UIN always identifies. Applies to every read at once; nothing is read again.</p></form></div></details>`;
  }

  async function refresh() {
    clearTimeout(poll);
    const data = await api(base);
    if (!host.isConnected) return;
    const found = data.detected, none = found && !found.sheets && !found.unsure && !found.pending;
    const seen = !found ? "" : found.sheets ? `<p data-bubble-detected><strong>${found.sheets} page${found.sheets === 1 ? "" : "s"}</strong> of this scan ${found.sheets === 1 ? "was" : "were"} recognized as bubble sheets by their positioning marks${found.unsure ? `; ${found.unsure} more only resemble${found.unsure === 1 ? "s" : ""} one` : ""}.</p>`
      : found.pending ? '<p data-bubble-detected class="field-hint">Checking this scan\'s pages for bubble sheets…</p>'
      : `<p data-bubble-detected class="field-hint">No page of this scan was recognized as a bubble sheet${found.unsure ? `, though ${found.unsure} resemble${found.unsure === 1 ? "s" : ""} one` : ""}.</p>`;
    const intro = `<p class="field-hint">Reads the filled bubbles, the version and whose ${escape(data.template)} sheet each is, for your own records; anything unclear waits for your review.</p>`;
    const relevant = !!data.run || !none;
    const place = slot(relevant ? "first" : "last");
    if (place && host.parentNode !== place) place.append(host);
    const formOpen = host.querySelector("[data-bubble-form]")?.open ?? (relevant && !data.run);
    const readingOpen = host.querySelector("[data-bubble-reading-details]")?.open ?? false;
    const settingsOpen = host.querySelector("[data-bubble-identification-settings]")?.open ?? false;
    const complete = data.run?.status === "complete";
    // A scan with no bubble sheet keeps the reader to one line; it can still be opened to read pages the marks did not show.
    host.innerHTML = relevant
      ? `${stage ? "" : '<h3 class="bubble-panel-title">Bubble sheets</h3>'}${complete ? (found?.unsure ? `<div class="callout bubble-notice" data-tone="warning"><p>${plural(found.unsure, "scan page")} only ${found.unsure === 1 ? "resembles" : "resemble"} a bubble sheet.</p></div>` : "") : seen}${data.run ? runMarkup(data.run, { seen, readingOpen, settingsOpen }) : ""}<details class="disclosure bubble-details" data-bubble-form ${formOpen ? "open" : ""}><summary>${data.run ? "Read again with different choices" : "Read this scan's bubble sheets"}</summary><div class="bubble-detail-content">${intro}${formMarkup(data)}</div></details>`
      : `<details class="disclosure bubble-details" data-bubble-form ${formOpen ? "open" : ""}><summary><strong>Bubble sheets</strong> <small>none recognized in this scan${found.unsure ? `, though ${found.unsure} page${found.unsure === 1 ? " resembles" : "s resemble"} one` : ""}</small></summary><div class="bubble-detail-content">${intro}${formMarkup(data)}</div></details>`;
    host.classList.toggle("bubble-sheets-none", !relevant);
    bind(data);
    // The summary card above tells the instructor what is left: let it know when a read starts, finishes or is reviewed.
    const state = JSON.stringify([data.run?.id, data.run?.status, data.run?.counts, data.detected]);
    if (lastState !== null && state !== lastState) root.querySelector(".document-scan-review")?.dispatchEvent(new CustomEvent("scan-changed"));
    lastState = state;
    if ((data.run && ["pending", "running"].includes(data.run.status)) || (found?.pending > 0 && data.can_request && checks++ < 40)) poll = setTimeout(look, 4000);
  }
  // The timed look while sheets are being read: none while the tab or the Inbox is hidden (it waits, then looks), and a failed read
  // is tried again in 15 s instead of ending the updates for good (2026-10-02).
  function look() {
    whenVisible(() => {
      if (!host.isConnected) return;
      if (host.closest("[hidden]")) { poll = setTimeout(look, 4000); return; }
      refresh().catch(() => { if (host.isConnected) { clearTimeout(poll); poll = setTimeout(look, 15000); } });
    });
  }

  function bind(data) {
    const form = host.querySelector("form[data-bubble-request]");
    form.elements.assignment_id.onchange = () => { form.outerHTML = formMarkup(data); bind(data); };
    form.onsubmit = async (event) => {
      event.preventDefault();
      const button = form.querySelector("button");
      if (button.disabled) return;
      setBusy(button, true);
      try {
        await api(base, "POST", { assignment_id: form.elements.assignment_id.value, question_count: form.elements.question_count.value === "" ? null : Number(form.elements.question_count.value),
        });
        host.querySelector("[data-bubble-form]").open = false;
        filter = null;
        await refresh();
      } catch (cause) { toast(cause.message, true); setBusy(button, false); }
    };
    const select = host.querySelector("[data-bubble-filter]");
    if (select) select.onchange = () => { filter = select.value; refresh().catch((cause) => toast(cause.message, true)); };
    host.querySelectorAll("[data-bubble-sheet]").forEach((button) => { button.onclick = () => openSheet(button.dataset.bubbleSheet).catch((cause) => toast(cause.message, true)); });
    const threshold = host.querySelector("form[data-bubble-threshold]");
    if (threshold) threshold.onsubmit = async (event) => {
      event.preventDefault();
      const button = threshold.querySelector("button");
      if (button.disabled) return;
      setBusy(button, true);
      try {
        const saved = await api("/api/bubble-sheets/settings", "POST", { auto_identify: Number(threshold.elements.auto_identify.value) / 100 });
        toast(`Students are now identified automatically at ${Math.round(saved.auto_identify * 100)}% or more.`);
        await refresh();
      } catch (cause) { toast(cause.message, true); setBusy(button, false); }
    };
    const all = host.querySelector("[data-bubble-review-all]");
    if (all) all.onclick = () => { all.disabled = true; void reviewAll().finally(() => { all.disabled = false; }); };
  }

  async function openSheet(id, queue = null) {
    const intended = captureDialogIntent(document.querySelector("#dialog"));
    const sheet = await api(`/api/bubble-sheets/sheets/${encodeURIComponent(id)}`);
    if (!intended() || !host.isConnected) return;
    const current = sheet.current, reading = sheet.reading, flags = current.flags, candidates = sheet.match.candidates || [];
    const states = new Map((reading.answers || []).map((item) => [item.question, item]));
    // Each thing the instructor is asked about is shown as the piece of the scan it came from, not left to be found on the page.
    const parts = `/api/bubble-sheets/sheets/${encodeURIComponent(id)}/parts`;
    const crop = (part, label) => reading.aligned ? `<img class="bubble-crop" loading="lazy" src="${parts}/${part}" alt="${escape(label)} on the scan">` : "";
    // A snapshot of bubbles is also where the answer is given: a click target lies over each bubble (placed after the markup is
    // in the page, since the Content Security Policy allows no inline styles). The field beside it stays, and the two follow each other.
    const pick = (part, label) => !reading.aligned ? "" : `<span class="bubble-pick" data-pick="${part}">${crop(part, label)}${(sheet.targets?.[part === "version" ? "version" : "question"] || []).map((zone) =>
      `<button type="button" class="bubble-choice" data-letter="${zone.letter}" data-left="${zone.left}" data-width="${zone.width}" aria-pressed="false" aria-label="${escape(label)}: ${zone.letter}"></button>`).join("")}</span>`;
    // A student already recorded for another sheet of this assignment is not offered: never preselected, and set apart at the end of the
    // picker, where the instructor can still choose them to correct a wrong match.
    const takenIds = new Set(sheet.students.filter((student) => student.taken).map((student) => student.enrollment_id));
    const free = candidates.filter((item) => !item.taken), first = free.map((item) => item.enrollment_id);
    // Nothing identified: the picker starts on the closest student still free (the suggestion when there is one), with its
    // confidence beside the name, rather than on nobody. It is recorded only when the instructor saves the review.
    const suggested = sheet.match.state === "suggested" && !takenIds.has(sheet.match.enrollment_id) ? sheet.match.enrollment_id : !current.reviewed_at && free.length ? free[0].enrollment_id : null;
    // A control that needs review is open and editable. A confident one is locked inside a collapsed section, so it cannot be changed by accident.
    const lock = (needs) => needs ? "" : "disabled data-locked";
    // A searchable picker (requirement, 2026-09-24): the read's own roster (the sections chosen for the scan's assessment), the
    // closest students first; digits typed are matched against UINs on the server, which sends back names and sections only.
    const studentValue = (student) => `${student.section_id}|${student.enrollment_id}`;
    const studentText = (student) => `${student.name} · Section ${student.section_number}`;
    const preselected = sheet.students.find((student) => student.enrollment_id === (current.enrollment_id || suggested));
    let chooser = null;
    const studentChooser = (needs) => chooser = studentPicker(escape, {id: `bubble-student-${id}`, name: "student", label: "Student",
      value: preselected ? studentValue(preselected) : "", locked: !needs,
      search: (digits) => api(`/api/bubble-sheets/sheets/${encodeURIComponent(id)}/students`, "POST", { uin: digits })
        .then((result) => result.students.map((student) => ({ value: studentValue(student), text: studentText(student), note: `UIN ${Math.round(student.similarity * 100)}%` }))),
      groups: [{ label: "", options: [{ value: "", text: "No student recorded" }] },
        ...(free.length ? [{ label: "Closest to what was read", options: free.map((item) => ({ value: studentValue(item), text: studentText(item), note: `${Math.round(item.confidence * 100)}%` })) }] : []),
        { label: "Everyone else in the chosen sections", options: sheet.students.filter((student) => !student.taken && !first.includes(student.enrollment_id)).map((student) => ({ value: studentValue(student), text: studentText(student) })) },
        ...(takenIds.size ? [{ label: "Already matched to another sheet", options: sheet.students.filter((student) => student.taken).map((student) => ({ value: studentValue(student), text: studentText(student) })) }] : [])]});
    const studentField = (needs) => `<div class="bubble-item" data-item="student"><div class="bubble-identity">${crop("name", "Name box")}${crop("id", "ID box")}</div>
      <p class="field-hint">Read from the sheet: ID “${escape(sheet.id_text || "nothing")}”, name “${escape(sheet.name_text || "nothing")}”.${!current.enrollment_id && !current.reviewed_at && suggested ? " The closest student still free is preselected; check it against the snapshots before saving." : ""}${sheet.match.state === "matched" ? (sheet.match.method === "label" ? " The sheet's Scantron label was printed for this student." : " The UIN matches this student exactly.") : current.identified_by === "confidence" ? ` Identified automatically from ${{ name: "the name read", uin: "the digits read" }[sheet.match.method] || "the digits and the name read"}, at ${Math.round(sheet.match.confidence * 100)}% confidence.` : ""}</p>
      <div class="field bubble-student-field"><span class="field-label" aria-hidden="true">Student</span>${studentChooser(needs).markup}</div></div>`;
    const versionField = (needs) => `<div class="bubble-item" data-item="version">${pick("version", "Version row")}<label class="field">Version<select name="version" aria-label="Version" ${lock(needs)}><option value="">None</option>${[..."ABCDE"].map((letter) => `<option ${current.version === letter ? "selected" : ""}>${letter}</option>`).join("")}</select></label></div>`;
    const questionField = (number, needs) => {
      const item = states.get(number), value = current.answers[number - 1] ?? "";
      const why = needs && item ? { unsure: `faint or erased mark at ${item.unsure}${item.answer ? ` beside ${item.answer}` : ""}` }[item.state] || "" : needs ? "not read: enter it from the page" : "";
      return `<div class="bubble-answer ${needs ? "flagged" : ""}" data-item="q${number}"><label><span>${number}</span><input name="answer" data-question="${number}" value="${escape(value)}" maxlength="5" size="4" autocomplete="off" spellcheck="false" aria-label="Question ${number} answer" pattern="[A-Ea-e]*" ${lock(needs)}></label>${why ? `<small>${escape(why)}</small>` : ""}${needs ? pick(`q${number}`, `Question ${number}`) : reading.aligned ? `<button type="button" class="text-button bubble-show" data-show="q${number}" aria-label="Show question ${number} on the scan">show</button>` : ""}</div>`;
    };
    const numbers = Array.from({ length: sheet.question_count }, (_, index) => index + 1);
    const flagged = numbers.filter((number) => flags.unread || flags.questions.includes(number)), confident = numbers.filter((number) => !flagged.includes(number));
    const open = [flags.identity ? studentField(true) : "", flags.version ? versionField(true) : "", flagged.length ? `<div class="bubble-answers">${flagged.map((number) => questionField(number, true)).join("")}</div>` : ""].join("");
    const kept = [!flags.identity ? "the student" : "", !flags.version ? "the version" : "", confident.length ? plural(confident.length, "question") : ""].filter(Boolean);
    showDialog(`<form id="bubble-review" ${queue ? "data-review-all" : ""}>${dialogHeader(`Bubble sheet on page ${sheet.source_page_index}`, queue ? `Reviewing all · ${plural(queue.left, "sheet")} left in this scan` : escape(STATUS[current.status]))}<div class="dialog-body bubble-review"><div class="bubble-page ${reading.rotation ? "upside-down" : ""}"><img src="/api/inbox/artifacts/${encodeURIComponent(sheet.raster_artifact_id)}/download" alt="Scanned bubble sheet, page ${sheet.source_page_index}"></div><div class="bubble-fields">
      <section data-needs-review><h3>${open ? "Needs your review" : current.reviewed_at ? "Reviewed" : "Nothing on this sheet needs review"}</h3>
      ${current.reasons.length ? `<ul class="seating-issue-list" role="alert">${current.reasons.map((reason) => `<li>${escape(reason)}</li>`).join("")}</ul>` : ""}${open}
      ${open ? '<p class="field-hint">Click the bubble that counts, right on the snapshot; click it again for a blank, and Shift-click to add a second one when two really are filled. Typing A to E in the field works too.</p>' : ""}</section>
      ${kept.length ? `<details class="bubble-confident" data-confident><summary>${current.reviewed_at ? "Recorded values" : "Confident reads"}: ${escape(kept.join(", "))}</summary>
        <p class="field-hint">These were read without doubt${current.reviewed_at ? " or already reviewed" : ""} and are locked so they cannot change by accident. Use “show” to compare a question with the scan. Unlock only to enter something yourself.</p>
        <button type="button" class="button" data-unlock>Unlock these to edit</button>
        ${!flags.identity ? studentField(false) : ""}${!flags.version ? versionField(false) : ""}${confident.length ? `<div class="bubble-answers">${confident.map((number) => questionField(number, false)).join("")}</div>` : ""}</details>` : ""}
      <label class="field">Note<span class="field-optional">optional</span><input name="note" maxlength="500" value="${escape(current.note || "")}"></label>
      <p class="form-error" role="alert" hidden></p></div></div><footer class="dialog-footer">${queue ? '<span class="field-hint bubble-queue-note">Each review is saved as you go. Stop any time; Review all resumes at the next unresolved sheet.</span><button type="button" class="button" data-action="close">Stop</button><button type="button" class="button" data-bubble-skip>Skip</button><button type="submit" class="button primary">Save and next</button>' : '<button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Save review</button>'}</footer></form>`);
    const dialog = document.querySelector("#dialog");
    dialog.classList.add("dialog-wide");
    dialog.addEventListener("close", () => {
      dialog.classList.remove("dialog-wide");
      if (session) { session = null; refresh().catch(() => {}); }  // Stopped part-way: the list behind shows what was saved.
    }, { once: true });
    const form = document.querySelector("#bubble-review");
    const active = dialogSession(form, dialog);
    chooser?.bind(form);
    // Click targets over the bubbles. The field they belong to is the question's input or the version select; a locked
    // (confident) field cannot be changed by a click either.
    const bindPicks = (scope) => scope.querySelectorAll(".bubble-pick").forEach((holder) => {
      if (holder.dataset.bound) return;
      holder.dataset.bound = "1";
      const part = holder.dataset.pick, field = part === "version" ? form.elements.version : form.querySelector(`input[data-question="${part.slice(1)}"]`);
      const buttons = [...holder.querySelectorAll(".bubble-choice")];
      const letters = () => [...new Set(field.value.toUpperCase().replace(/[^A-E]/g, ""))];
      const show = () => buttons.forEach((button) => { button.setAttribute("aria-pressed", String(letters().includes(button.dataset.letter))); button.disabled = field.disabled; });
      buttons.forEach((button) => {
        button.style.left = `${button.dataset.left}%`;
        button.style.width = `${button.dataset.width}%`;
        button.onclick = (event) => {
          const letter = button.dataset.letter, chosen = letters();
          const next = part !== "version" && event.shiftKey ? (chosen.includes(letter) ? chosen.filter((item) => item !== letter) : [...chosen, letter])
            : chosen.length === 1 && chosen[0] === letter ? [] : [letter];
          field.value = next.sort().join("");
          field.dispatchEvent(new Event("input", { bubbles: true }));
          show();
        };
      });
      field.addEventListener("input", show);
      field.addEventListener("change", show);
      show();
    });
    bindPicks(form);
    if (queue) {
      // Straight to what is in doubt: type the answer, press Enter, and the next sheet comes up.
      form.querySelector("[data-needs-review] input:not([disabled]):not([type=hidden]), [data-needs-review] select:not([disabled])")?.focus();
      form.querySelector("[data-bubble-skip]").onclick = async (event) => {
        setBusy(event.currentTarget, true);
        session.skipped += 1;
        try { await reviewNext(sheet.source_page_index); } catch (cause) { toast(cause.message, true); }
      };
    }
    form.querySelectorAll("[data-show]").forEach((button) => { button.onclick = () => {
      const holder = document.createElement("span"), label = button.getAttribute("aria-label").replace("Show ", "");
      holder.innerHTML = pick(button.dataset.show, label);  // `label` is fixed text built here, never data.
      holder.querySelector("img").alt = label;
      button.closest(".bubble-answer").classList.add("wide");  // A compared row gets the room a flagged one has.
      button.replaceWith(holder.firstElementChild);
      bindPicks(form);
    }; });
    const unlock = form.querySelector("[data-unlock]");
    if (unlock) unlock.onclick = () => {
      form.querySelectorAll("[data-locked]").forEach((control) => { control.disabled = false; control.removeAttribute("data-locked"); control.dispatchEvent(new Event("change")); });
      unlock.replaceWith(Object.assign(document.createElement("p"), { className: "field-hint", textContent: "Unlocked: changes here replace what was read." }));
    };
    form.onsubmit = async (event) => {
      event.preventDefault();
      const button = form.querySelector('button[type="submit"]'), error = form.querySelector(".form-error");
      if (button.disabled) return;
      setBusy(button, true);
      error.hidden = true;
      // Locked controls still hold the confident values; every answer is sent in question order wherever it sits on the screen.
      const [section_id, enrollment_id] = form.elements.student.value ? form.elements.student.value.split("|") : [null, null];
      try {
        await api(`/api/bubble-sheets/sheets/${encodeURIComponent(id)}/review`, "POST", { section_id, enrollment_id, version: form.elements.version.value, note: form.elements.note.value,
          answers: numbers.map((number) => [...new Set(form.querySelector(`input[data-question="${number}"]`).value.toUpperCase().replace(/[^A-E]/g, ""))].sort().join("")) });
        if (!active()) return;
        if (queue && session) {
          session.saved += 1;
          await reviewNext(sheet.source_page_index);
          return;
        }
        dialog.close();
        toast("Review saved.");
        await refresh();
      } catch (cause) { error.textContent = cause.message; error.hidden = false; setBusy(button, false); }
    };
  }

  try { await refresh(); } catch (cause) { host.textContent = cause.message; }
  // Linked to one sheet (`#inbox/job/<id>/sheets?sheet=<sheet id>`): open its review once, as a click on it would.
  if (sheet) try { await openSheet(sheet); } catch (cause) { toast(cause.message); }
}
