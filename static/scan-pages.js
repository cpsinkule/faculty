// The scan's Inbox pages (its card and one page per flow stage), moved out of documents.js unchanged (2026-09-26, UX overhaul
// wave 1) so the Inbox owns them. inbox.js mounts them; nothing else imports this module.
import {bindScanGrading} from "./scan-grading.js";
import {studentPicker, keepPickerTyping} from "./student-picker.js";
import {revealCurrentStage, scanCard, scanFlowNav, scanNextStep, stageHref} from "./scan-flow.js";
import {patchChildren} from "./dom-patch.js";
import {calloutMarkup, captureDialogIntent, dialogSession, disclosureMarkup, plural, setBusy} from "./ui.js";
import { viewLink } from "./file-viewer.js";
import {cropButton, mountCropZoom} from "./grading-crops.js";

// Browser memory only: what the instructor opened or typed in the version sort
// panel, keyed by message, so background job refreshes do not discard it.
const sortViewState = new Map();
const unlabeledViewState = new Map();

// One request ID per dialog opened: a click sent again after a lost answer repeats the same request, never a second one.
const nonce = () => [...crypto.getRandomValues(new Uint8Array(16))].map(value => value.toString(16).padStart(2, "0")).join("");
// Why a page belongs to no paper, and why a held paper is grouped by hand: the usual answers, and the instructor's own words.
const ASIDE_REASONS = ["Section header sheet", "Scratch paper", "Cover or instruction sheet"];
const HELD_REASONS = ["A student added a sheet", "A sheet was scanned twice"];

// `page` draws one of the scan's Find students pages ("students" or "matched") instead of its summary and tools.
// `onItem(item)` hears every scan review item drawn (the Inbox names the page's header from its overview).
export async function mountScanReview({root, job, api, escape, toast, showDialog = null, dialogHeader = null, page = null, onItem = null}) {
  if (job.action !== "scan.receive") return;
  // A scan is its flow graph (requirement, 2026-09-24). Its own page shows the scan as a card, like an assignment
  // card in Grading; each stage has a page of its own with the graph on top. The bubble sheet panel mounts itself into a slot that
  // refreshes never rewrite, on the Bubble sheets page.
  const host = document.createElement("section"); host.className = "document-scan-review";
  const marker = page === "papers" ? "data-papers-page" : ["students", "matched"].includes(page) ? "data-students-page" : "";
  // The Bubble sheets page is one frame: the stage's words and its Review all, then the sheets' own panel below them in the same frame.
  host.innerHTML = page === "sheets"
    ? `<div data-scan-flow></div><div class="course-panel students-page scan-sheets-frame"><div data-scan-content data-scan-subpage data-scan-page="sheets"></div><div data-bubble-slot="last"></div></div>`
    : `${page ? "<div data-scan-flow></div>" : ""}<div data-scan-content ${page ? `class="course-panel students-page" data-scan-subpage data-scan-page="${page}" ${marker}` : ""}></div>`;
  const content = host.querySelector("[data-scan-content]"), flow = host.querySelector("[data-scan-flow]");
  const record = root.querySelector("[data-inbox-record]");
  if (record) record.before(host); else root.querySelector(".inbox-job-detail").append(host);
  host.addEventListener("scan-changed", () => { refresh().catch(() => {}); });
  const sectionList = numbers => numbers.length === 1 ? `Section ${numbers[0]}` : `Sections ${numbers.slice(0, -1).join(", ")} and ${numbers[numbers.length - 1]}`;
  // Paper printed for the whole term (one stack, requirement, 2026-10-01) names no section: its copy's section is its student's.
  const placeOf = number => number == null ? "One stack" : `Section ${number}`;
  const pageImageUrl = (message, id) => `/api/documents/messages/${escape(message)}/pages/${escape(id)}/image.png`;
  function markCount(page) {
    const candidates = JSON.parse(page.candidates_json);
    if (page.status !== "resolved" || candidates.length !== 1) return "";
    const count = candidates[0].locations?.length || 1;
    return `<p class="field-hint">Its label was read ${count === 1 ? "once" : `${count} times`} on this page.</p>`;
  }
  // Label recovery in plain words (2026-10-18): the "A1-…" alias is the short code printed beside each label, read by local OCR when
  // the square mark cannot be. `open` draws the form open on a page that needs it (Pages that need a look), not three folds deep.
  function recovery(page, open = false) {
    const reading = page.alias_ocr;
    const suggestion = !page.page_stamp_id && page.status === "unreadable" && reading?.result?.outcome === "suggested" ? reading.result : null;
    let status = "";
    if (reading) {
      if (["pending", "running"].includes(reading.status)) status = "Reading the code printed beside its label…";
      if (reading.status === "failed") status = reading.error;
      if (reading.status === "complete" && reading.result.outcome === "resolved" && page.status === "resolved" &&
          reading.result.match?.stamp_id === page.page_stamp_id) {
        status = `Identified from the code printed beside its label, ${reading.result.alias}.`;
      }
      if (reading.status === "complete" && !page.page_stamp_id) {
        status = {suggested: "The code printed beside its label matches a printed page. Compare it with this page before saving.",
          conflict: "The code beside its label read two ways. Look at the page and type the code yourself.",
          no_match: "No current printed page has the code read beside its label. Look at the page and type the code yourself."}[reading.result.outcome] || "";
      }
    }
    const match = suggestion?.match;
    // The reading's words, report and retry share one box, so the fold after it keeps its place (and the instructor's open state
    // and typing) when the reading moves on (dom-patch.js matches children by position).
    return `<div class="document-alias-reading">${status ? `<p class="field-hint" data-alias-ocr-status>${escape(status)}</p>` : ""}${reading?.artifact_id ? `<p><a ${viewLink(`/api/inbox/artifacts/${reading.artifact_id}/download`, "Label reading report", escape)}>Label reading report</a></p>` : ""}${reading?.can_retry ? `<button class="button" type="button" data-alias-ocr-retry="${page.id}">Read the code again</button>` : ""}</div>
      <details data-alias-recover ${suggestion || open ? "open" : ""}><summary>${suggestion ? "Check the code that was read" : "Recover its label from the printed code"}</summary>
      ${match ? `<p>${escape(match.code)} · ${escape(match.term === "Other" ? match.term_other : match.term)} ${match.year} · ${escape(placeOf(match.section_number))} · ${escape(match.title)} ${escape(match.version_label)} · Copy ${match.copy_number}, page ${match.page_number}</p>` : ""}
      <form data-recover="${page.id}"><label class="field">Code printed beside the label (A1-…)<input name="alias" required pattern="A1-[0-9A-HJKMNP-TV-Z]{10}" maxlength="13" value="${escape(suggestion?.alias || "")}"></label><label class="field">Why<input name="reason" required maxlength="1000"></label><button class="button" type="submit">Record correction</button></form></details>`;
  }
  function orientation(p) {
    const how = {label: "from its label", quiz: "with its quiz", text: "from readable text", unknown: "unknown, left as scanned", off: "auto-rotate off"}[p.orientation] || "";
    return p.rotation ? `Rotated 180° (${how})` : `As scanned${p.orientation === "unknown" || p.orientation === "off" ? ` (${how})` : ""}`;
  }
  // `surfaced`: the scan's step asks for sorting (nothing printed says what its pages are), so the panel is drawn open on the Scan
  // page itself with its settings, not under Other options (2026-10-18: it sat three folds deep).
  function versionSort(item, surfaced = false) {
    const vs = item.version_sort;
    if (!vs) return "";
    const latest = vs.latest, s = latest?.settings || {};
    // Live polling re-renders this panel; keep the instructor's open sections and typed settings.
    const saved = sortViewState.get(job.message_id) || {};
    const previous = host.querySelector("form[data-version-sort]");
    const keep = name => previous?.elements[name]?.value ?? saved[name];
    // Folded until the overview's step brings it into view (Show), except while a sort the instructor asked for is under way.
    const openPanel = host.querySelector("[data-version-sort-panel]")?.open ?? saved.openPanel ?? (surfaced || ["pending", "running"].includes(latest?.status));
    const openSettings = host.querySelector("[data-version-sort-settings]")?.open ?? saved.openSettings ?? !latest;
    const openPages = host.querySelector("[data-version-sort-pages]")?.open ?? saved.openPages ?? false;
    const preset = keep("preset") || s.preset || "version-letter", band = keep("band") || s.band || "top";
    const pagesPerQuiz = keep("pages_per_quiz") || s.pages_per_quiz || 2;
    const pattern = keep("pattern") ?? (s.preset === "custom" ? s.pattern : "");
    const autoRotate = previous ? previous.elements.auto_rotate.checked : (saved.auto_rotate ?? s.auto_rotate ?? true);
    let status = "";
    if (latest) {
      const total = latest.pages_total, done = latest.reads.complete;
      if (latest.status === "pending") status = `Queued for sorting · position ${vs.queue.position} of ${vs.queue.live} · ${total} pages.`;
      else if (latest.status === "running") status = `Reading printed labels · ${done} of ${total} pages read${vs.queue.position > 1 ? ` · position ${vs.queue.position} of ${vs.queue.live}` : ""}.`;
      else if (latest.status === "failed") status = `${latest.error} ${done} of ${total} pages were read.`;
      else {
        const versions = latest.result.outputs.filter(o => o.key).length;
        status = `Sorted ${total} pages into ${versions} version${versions === 1 ? "" : "s"}; ${latest.result.unsorted_pages} unsorted; ${latest.result.rotated_pages || 0} turned right way up.`;
      }
    }
    const results = latest?.status === "complete" ? `<ul class="document-version-outputs">${latest.result.outputs.map(o => `<li><strong>${escape(o.label)}</strong> · ${o.pages} page${o.pages === 1 ? "" : "s"}${o.key ? ` · ${o.labeled_pages} labeled` : ""} · <a ${viewLink(`/api/inbox/artifacts/${o.artifact_id}/download`, o.label || "Version PDF", escape)}>PDF</a></li>`).join("")}</ul>
      <p><a ${viewLink(`/api/inbox/artifacts/${latest.result.report_artifact_id}/download`, "Version sort report", escape)}>Version sort report</a></p>
      <details data-version-sort-pages ${openPages ? "open" : ""}><summary>Page-by-page readings</summary><div class="document-version-pages-wrap"><table class="document-version-pages"><thead><tr><th>Page</th><th>Reading</th><th>Sorted into</th><th>Orientation</th><th>Why</th></tr></thead><tbody>${latest.result.pages.map(p => `<tr><td>${p.attachment_page}</td><td>${escape(p.outcome === "label" ? p.label : p.outcome)}</td><td>${escape(p.assigned ? `${s.prefix || ""}${p.assigned}` : "Unsorted")}</td><td>${escape(orientation(p))}</td><td>${escape(p.reason)}</td></tr>`).join("")}</tbody></table></div></details>` : "";
    const presetOptions = vs.presets.map(p => `<option value="${p.key}" ${preset === p.key ? "selected" : ""}>${escape(p.label)}</option>`).join("") + `<option value="custom" ${preset === "custom" ? "selected" : ""}>Custom pattern</option>`;
    const bandOptions = vs.bands.map(b => `<option value="${b.key}" ${band === b.key ? "selected" : ""}>${escape(b.label)}</option>`).join("");
    return `<details class="document-version-sort" data-version-sort-panel ${openPanel ? "open" : ""}><summary>Sort by printed version <small>Separate PDFs by version</small></summary>
      <p class="field-hint">For quizzes printed without Faculty's labels. Local OCR reads each page's label band, the printed version is matched exactly, and the unlabeled sides that follow stay with it. Sheets fed upside down are turned the right way up. The result is one PDF per version plus an Unsorted PDF. Readings are drafts: no page is identified, approved or executed.</p>
      ${status ? `<p data-version-sort-status>${escape(status)}</p>` : ""}
      ${vs.runtime_available ? "" : '<p role="alert">Local OCR is not installed on this server.</p>'}
      ${results}
      ${vs.can_retry ? '<button class="button" data-version-sort-retry>Retry version sorting</button>' : ""}
      <details data-version-sort-settings ${openSettings ? "open" : ""}><summary>${latest ? "Sort again with different settings" : "Sorting settings"}</summary>
      <form data-version-sort><label class="field">Printed label<select name="preset">${presetOptions}</select></label>
      <label class="field" data-custom-pattern ${preset === "custom" ? "" : "hidden"}>Custom pattern (one capture group, case-insensitive)<input name="pattern" maxlength="200" value="${escape(pattern)}" placeholder="e.g. quiz\\s*([A-D])"></label>
      <label class="field">Pages per quiz<input name="pages_per_quiz" type="number" min="1" max="${vs.max_pages_per_quiz}" value="${escape(String(pagesPerQuiz))}" required></label>
      <p class="field-hint">Two-sided quizzes scanned front then back are 2 pages per quiz; blank backs count.</p>
      <label class="field">Where the label is printed<select name="band">${bandOptions}</select></label>
      <label><input type="checkbox" name="auto_rotate" ${autoRotate ? "checked" : ""}> Auto-rotate upside-down pages</label>
      <button class="button primary" type="submit" ${vs.can_request ? "" : "disabled"}>Queue version sorting</button>
      ${vs.queue.live ? `<p class="field-hint">Sorting queue: ${vs.queue.live} of ${vs.queue.capacity} scans in progress.</p>` : ""}
      </form></details></details>`;
  }
  // The last first-page-label preview; a background refresh of this panel must not discard what is being reviewed.
  let sequencePlan = null, gradingAssociation = null, sequencePreviewGeneration = 0;
  function sequencePlanMarkup(secondary = false) {
    if (!sequencePlan) return "";
    const c = sequencePlan.counts;
    const result = exam => exam.problems.length ? `<strong>Not grouped.</strong> ${exam.problems.map(escape).join(" ")}`
      : !exam.ready ? "Already grouped"
      : `Ready${exam.turned ? ` (its sheet was scanned back side first: page ${exam.turned} is its page 2)` : ""}${exam.group ? `: ${plural(exam.sides, "side")} become this student's paper` : ""}${exam.assign.length ? `${exam.group ? ";" : ":"} ${exam.assign.map(a => `page ${a.source_page_index} → ${a.page_number}`).join(", ")}` : ""}`;
    const action = `${c.grouped ? `Group ${plural(c.grouped, "paper")}` : ""}${c.grouped && c.identified ? " and identify " : c.identified ? "Identify " : ""}${c.identified ? plural(c.identified, "page") : ""}` || "Nothing to group";
    return `<p data-sequence-counts><strong>${plural(c.ready, "paper")} ready</strong> (${plural(c.grouped, "paper")} to group, ${plural(c.identified, "page")} to identify) · ${c.complete} already grouped · ${c.review} need${c.review === 1 ? "s" : ""} review · ${plural(c.unlabeled, "page")} outside any labeled paper.</p>
      <div class="document-version-pages-wrap"><table class="document-version-pages"><thead><tr><th>Label</th><th>Attachment pages</th><th>Sides</th><th>Written</th><th>Result</th></tr></thead><tbody>${sequencePlan.exams.map(exam => `<tr><td>Copy ${exam.copy_number}${exam.caption ? ` · ${escape(exam.caption)}` : exam.enrollment_id ? ` · ${escape(exam.enrollment_id)}` : ""}<br><small>${escape(exam.title)} ${escape(exam.version_label)}</small></td><td>${exam.first_page}–${exam.last_page}</td><td>${exam.sides}</td><td>${exam.written}</td><td>${result(exam)}</td></tr>`).join("") || '<tr><td colspan="5">No readable first-page labels in this scan.</td></tr>'}</tbody></table></div>
      ${sequencePlan.runs.length ? `<p><strong>Pages outside any labeled paper</strong></p><ul>${sequencePlan.runs.map(run => `<li>Attachment pages ${run.first_page}–${run.last_page} (${run.written} written) ${escape(run.where)}.</li>`).join("")}</ul>` : ""}
      <p class="field-hint">Papers that do not add up are left exactly as they are. Each ready paper is kept as that student's paper, in scan order. Behind a first-page label, each side of a paper that adds up takes its position as its page (side 3 is page 3); blank backs keep their place. The scan's Review then saves each such paper as that student's reviewed paper. Labels already read, reviewed papers and the scan's Review do not change.</p>
      <button class="button${secondary ? "" : " primary"}" type="button" data-sequence-apply ${c.ready ? "" : "disabled"}>${action}</button>`;
  }
  // The scan's grouping in two parts (2026-10-18, one way to group on Papers): `records`, how it was grouped (each run's Undo) and the
  // grouped papers, drawn on the page; and `manual`, grouping at a number of sides per paper with its preview, one of the "Other ways
  // to group". Both empty when the scan has no first-page labels to group by.
  function sequencePanel(item, secondary = false) {
    const seq = item.sequence;
    if (!seq || (!seq.runs.length && !(seq.labels && seq.unidentified))) return {records: "", manual: ""};
    const sides = host.querySelector("form[data-sequence] input")?.value || sequencePlan?.sides || seq.suggested_sides || "";
    const active = seq.runs.filter(run => !run.undone_at).length;
    // How the exams were grouped, with each run's Undo: open while the instructor previews a grouping or nothing grouped itself yet,
    // folded once the scan grouped itself (the overview's step already says how many), and kept as the instructor leaves it.
    const historyOpen = host.querySelector("[data-sequence-history]")?.open ?? (!!sequencePlan || (!secondary && !active));
    const records = seq.runs.length || seq.packets.length ? `<section class="document-review-panel document-sequence" data-sequence-records><h4>Grouping so far</h4>
      ${seq.runs.length ? `<details data-sequence-history ${historyOpen ? "open" : ""}><summary>How they were grouped · ${seq.runs.length} run${seq.runs.length === 1 ? "" : "s"}${active ? ", with undo" : ""}</summary>` : ""}
      ${seq.runs.map(run => `<p data-sequence-run>${escape(new Date(run.created_at).toLocaleString())}: ${run.origin === "held_paper" ? `grouped copy ${escape(String(run.copy_number))} by hand from attachment pages ${escape((run.sides_chosen || []).join(", "))}${run.reason ? ` (${escape(run.reason)})` : ""}` : `${run.automatic ? "automatically, from the even spacing of the labels, " : ""}grouped ${run.packets ?? 0} paper${run.packets === 1 ? "" : "s"}${run.pages ? ` and identified ${run.pages} page${run.pages === 1 ? "" : "s"} by position` : ""} at ${run.sides} sides per paper`}${run.undone_at ? ` · undone (${run.undo.packets ?? 0} paper${run.undo.packets === 1 ? "" : "s"} ungrouped, ${run.undo.cleared} page${run.undo.cleared === 1 ? "" : "s"} cleared${run.undo.kept.length ? `, ${run.undo.kept.length} kept` : ""})` : ` <button class="text-button" type="button" data-sequence-undo="${escape(run.id)}">Undo</button>`}</p>`).join("")}${seq.runs.length ? "</details>" : ""}
      ${seq.packets.length ? `<details data-sequence-packets><summary>${seq.packets.length} grouped paper${seq.packets.length === 1 ? "" : "s"}</summary><ul class="document-version-outputs">${seq.packets.map(k => `<li>Copy ${k.copy_number}${k.caption ? ` · ${escape(k.caption)}` : k.enrollment_id ? ` · ${escape(k.enrollment_id)}` : ""} · ${escape(k.title)} · ${escape(placeOf(k.section_number))} · attachment pages ${k.first_page}–${k.last_page} · <a ${viewLink(`/api/documents/packets/${encodeURIComponent(k.id)}/download`, "Paper PDF", escape)}>Paper PDF</a></li>`).join("")}</ul></details>` : ""}</section>` : "";
    // The one place a scan's sides per paper is asked (2026-10-18): the Students search and the new-assignment dialog point here.
    const manual = `<section class="scan-other-way" data-sequence-manual><h5>${seq.packets.length ? "Group again at a number of sides per paper" : "Group by first-page label at a number of sides per paper"}</h5>
      <p class="field-hint">For papers labeled on the first page only: enter the sides per paper and preview.</p>
      ${disclosureMarkup({label: "How grouping works", key: "inbox:sequence-how", body: "<p>Evenly spaced labels are grouped on arrival. Otherwise each page 1 label starts a paper, and the pages after it join it only when the next label arrives after exactly the number of sides you enter (or after a whole number of further papers, when a label was not read). Blank backs count as sides. This works for exams, quizzes or any other assignment.</p>"})}
      <form data-sequence><label class="field">Scanned sides per paper<input name="sides" type="number" min="1" max="${seq.max_sides}" value="${escape(String(sides))}" required></label>
      <p class="field-hint">Count both sides when the papers were scanned two-sided; blank backs count.${seq.suggested_sides ? ` In this scan the labels are usually ${seq.suggested_sides} sides apart.` : ""}</p>
      <button class="button" type="submit">Preview</button></form>
      <div data-sequence-plan>${sequencePlanMarkup(secondary)}</div></section>`;
    return {records, manual};
  }
  // Exams printed without labels (2026-09-24): found on request, read by local OCR, matched to the roster, registered by the
  // instructor. What was read is untrusted text, escaped here; names appear only inside this panel's disclosure and on the
  // scan's Find students pages.
  const UNLABELED = {identified: "Identified", registered: "Registered", registering: "Waiting for its label", conflict: "Check: already has a paper",
    needs_review: "Check who wrote it", not_cover: "Not a first page"};
  // One searchable student picker per first page (requirement, 2026-09-24), bound after the page is drawn.
  const unlabeledPickers = new Map();
  const pct = value => `${Math.round((value || 0) * 100)}%`;
  // Which page a first page is listed on (requirement, 2026-09-24: a successful match should not have to be scrolled past to reach
  // the papers that still need a name). Find students: those only the instructor can settle, and the instructor's own choices until
  // they are registered. Matched papers: every paper with its student, identified, waiting for its label or registered, so a match
  // corrected there stays in view.
  const needsYou = c => ["needs_review", "conflict"].includes(c.state);
  const yourChoice = c => c.basis === "review" && ["identified", "not_cover"].includes(c.state);
  const isMatched = c => ["identified", "registering", "registered"].includes(c.state);
  const studentsHref = `#inbox/job/${job.id}/students`;
  function coverHow(c, u) {
    if (c.state === "registered" || c.state === "registering") return "";
    if (c.basis === "review") return "You chose this student.";
    if (c.basis === "uin") return "Identified by an exact roster UIN.";
    if (c.basis === "confidence") return `Identified at ${pct(c.match.confidence)} (your threshold is ${pct(u.threshold)}).`;
    if (c.basis === "jev") return `Identified by ${c.reader || "Jev"} from the handwritten name and UIN.`;
    return c.match.enrollment_id ? `Closest: ${c.match.candidates[0]?.name || ""} at ${pct(c.match.confidence)}, below your ${pct(u.threshold)} threshold.` : "No roster student is close to what was read.";
  }
  function coverChoice(c, u) {
    if (["registered", "registering"].includes(c.state)) return escape(c.student ? `${c.student.name} · section ${c.student.section_number}` : "");
    // Only the instructor's own choice (a student, or not a cover) selects it; going back to the reading shows "As read" again.
    const current = c.basis === "review" ? (c.state === "not_cover" ? "not_cover" : c.enrollment_id) : "read";
    const asRead = c.basis && c.basis !== "review" && c.student ? `As read: ${c.student.name}` : "As read: nobody identified";
    // The roster is the sections chosen for this scan's assessment; the students closest to what was read come first.
    const closest = (c.match?.candidates || []).filter(m => u.students.some(st => st.enrollment_id === m.enrollment_id));
    const picker = studentPicker(escape, {id: `unlabeled-${c.scan_page_id}`, label: `Student for the first page on attachment page ${c.page}`, value: current,
      search: digits => api(`/api/documents/messages/${job.message_id}/unlabeled/students`, "POST", {uin: digits})
        .then(r => r.students.map(st => ({value: st.enrollment_id, text: `${st.name} · section ${st.section_number}`, note: `UIN ${pct(st.similarity)}`}))),
      groups: [{label: "", options: [{value: "read", text: asRead}, {value: "not_cover", text: "Not a first page"}]},
        ...(closest.length ? [{label: "Closest to what was read", options: closest.map(m => ({value: m.enrollment_id, text: `${m.name} · section ${m.section_number}`, note: pct(m.confidence)}))}] : []),
        {label: "Students", options: u.students.filter(st => !closest.some(m => m.enrollment_id === st.enrollment_id))
          .map(st => ({value: st.enrollment_id, text: `${st.name} · section ${st.section_number}`}))}]});
    unlabeledPickers.set(c.scan_page_id, picker);
    return picker.markup;
  }
  // The top of a first page, enlarged on a click in the grading pages' crop viewer (cropButton escapes the URL and label).
  const firstPageCrop = (url, page) => `<div class="document-unlabeled-cover">${cropButton(url, `Top of the first page on attachment page ${page}`, {e: escape})}</div>`;
  const coverTable = (covers, u) => `<div class="document-version-pages-wrap"><table class="document-version-pages document-unlabeled"><thead><tr><th>Page</th><th>Printed version</th><th>First page</th><th>Student</th><th>State</th></tr></thead><tbody>${covers.map(c => `<tr data-unlabeled-row="${escape(c.scan_page_id)}" data-state="${escape(c.state)}"><td>${c.page}</td><td>${escape(c.version || "not read")}</td>
      <td>${firstPageCrop(`/api/documents/messages/${job.message_id}/unlabeled/${c.scan_page_id}/cover.png`, c.page)}
      <small>Read: ${escape(c.fields.name || "no name")} · UIN ${escape(c.fields.uin || "none")} · section ${escape(c.fields.section || "none")}</small></td>
      <td>${coverChoice(c, u)}</td><td>${c.other_paper && c.state === "conflict" ? `<button class="text-button unlabeled-other-open" type="button" data-unlabeled-other="${escape(c.scan_page_id)}" title="Show the other paper">${escape(UNLABELED[c.state])} ↗</button>` : `<strong>${escape(UNLABELED[c.state] || c.state)}</strong>`}<br><small>${escape(coverHow(c, u))}</small>${c.notes.map(n => `<br><small>${escape(n)}</small>`).join("")}${c.other_paper && c.state !== "conflict" ? `<br><button class="text-button" type="button" data-unlabeled-other="${escape(c.scan_page_id)}">See the other paper</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
  // The run's progress in one sentence ([data-unlabeled-status]); what it looks for is shown as facts beside it.
  function unlabeledStatus(u) {
    const run = u.run, counts = u.counts || {};
    if (!run) return "";
    if (["pending", "running"].includes(run.status)) return `Looking for first pages for ${run.title} · ${plural(run.pages_read, "page")} read, ${plural(run.covers_found, "first page")} found.`;
    if (["failed", "cancelled"].includes(run.status)) return "";  // the callout says why it stopped
    return `${plural(run.covers_found, "first page")} found: ${Object.entries(UNLABELED).filter(([key]) => counts[key]).map(([key, label]) => `${counts[key]} ${label.toLowerCase()}`).join(", ") || "nothing to register"}.`;
  }
  const runFacts = run => `<p class="chip-row students-page-facts" data-unlabeled-facts>${[run.title, `${plural(run.sides, "side")} per paper`,
    run.versions ? `Versions ${run.versions.length > 2 ? `${run.versions[0]}–${run.versions[run.versions.length - 1]}` : run.versions.split("").join(", ")}` : "",
    run.register_confident ? "Registers confident matches" : ""].filter(Boolean).map(fact => `<span class="chip">${escape(fact)}</span>`).join("")}</p>`;
  // A stopped or failed run says so, with what it got through, and Continue is the page's one primary action.
  const runControls = (run, primary = true) => `${run && ["failed", "cancelled"].includes(run.status) ? calloutMarkup({tone: "warning", className: "students-run-stopped",
      title: run.status === "cancelled" ? "Looking for first pages was stopped" : "Looking for first pages stopped",
      body: `${run.error ? `${run.error} ` : ""}${plural(run.pages_read, "page")} read · ${plural(run.covers_found, "first page")} found. What was read is kept; reading goes on from where it stopped.`,
      action: {label: "Continue looking for first pages", primary, data: {unlabeledContinue: ""}}}) : ""}
      ${run && ["pending", "running"].includes(run.status) ? '<p><button class="button" type="button" data-unlabeled-stop>Stop looking for first pages</button> <small class="field-hint">The page being read is finished and kept, like every page read before it.</small></p>' : ""}`;
  function registerButton(u) {
    const ready = u.covers.filter(c => c.state === "identified").length;
    return ready ? `<button class="button primary" type="button" data-unlabeled-register>Register ${plural(ready, "identified paper")}</button>` : "";
  }
  const REGISTER_HINT = "Registering gives each first page the page 1 identity of its student's label, as if the label had been stuck on: an issued label no scan has shown is reused, otherwise a new label job is issued for the student's section and never printed. The scan is then grouped at the sides per paper, and reviewing the scan assembles each paper. Registering also confirms each paper's printed version for grading when it is one of the assignment's versions; a paper whose pages match another version's blank exam still waits for your decision in grading.";
  // Sides per paper already answered (2026-10-18: it was asked in three forms): the answer and a Change fold holding the same field,
  // so a search again keeps the earlier count unless the instructor changes it. Papers is where the grouping's count is set.
  const sidesReference = (item, sides, field) => {
    const papers = stageOf(item, "papers"), open = host.querySelector("[data-sides-change]")?.open ?? false;
    return `<details class="scan-sides-change" data-sides-change ${open ? "open" : ""}><summary>${escape(plural(Number(item.unlabeled?.run?.sides || sides), "side"))} per paper, as the last search used · Change</summary>${field}</details>
      ${papers ? `<p class="field-hint">The grouping's sides per paper are set on <a href="${escape(stageHref(job.id, papers))}">Papers</a>.</p>` : ""}`;
  };
  // Asking for first pages to be found, or found again: the assignment and the sides per paper. Folded once a run exists.
  function unlabeledSettings(item) {
    const u = item.unlabeled;
    if (!u || (!u.run && !u.candidates)) return "";
    const run = u.run, saved = unlabeledViewState.get(job.message_id) || {};
    if (run && !["complete", "failed", "cancelled"].includes(run.status)) return "";
    const previous = host.querySelector("form[data-unlabeled]");
    const keep = name => previous?.elements[name]?.value ?? saved[name];
    // Never the first assignment in sort order (inbox-5): the run's, the scan's own, or nothing until the instructor chooses one.
    const chosen = keep("assignment_id") || run?.assignment_id || u.assignments.find(a => a.linked)?.id || "";
    const sides = keep("sides") || run?.sides || item.sequence?.suggested_sides || u.paper_sides || "";
    const open = host.querySelector("[data-unlabeled-settings]")?.open ?? saved.open ?? !run;
    // One paper's length from the assignment's blank templates, and what that makes of this scan: a suggestion the form starts from.
    const linked = u.assignments.find(a => a.linked), count = Number(sides), pages = u.candidates;
    const estimate = count > 0 && pages ? `${pages} page${pages === 1 ? "" : "s"} make ${pages % count ? "about " : ""}${plural(Math.max(1, Math.round(pages / count)), "paper")} of ${count} sides.` : "";
    const length = u.paper_sides && linked ? `${escape(linked.title)}'s blank template has ${u.paper_sides} pages. ` : "";
    return `<details class="disclosure document-unlabeled-panel students-page-group" data-unlabeled-settings ${open ? "open" : ""}><summary><span>${run ? "Look for first pages again" : "Find first pages"}</span></summary><div class="disclosure-body">
      ${run ? "" : '<p class="scan-unlabeled-lead">These pages carry no label, so each paper is found from its first page, and you are asked only about the papers Faculty is not sure of.</p>'}
      ${disclosureMarkup({label: "How first pages are read", key: "inbox:unlabeled-how", body: `<p>Local OCR reads the printed Name and UIN, the section and the printed version on each paper's first page and matches them to the roster. An exact roster UIN, or a match at your bubble-sheet threshold, identifies the student; a student who already has a scanned paper of this assignment, two first pages read as one student, and anything less wait for you. First pages are read where the side count says the next paper starts, and any page that looks like a first page found is read too. Registered papers go on to grading with the rest of the scan; anything unsure waits for you here.</p><p>${escape(REGISTER_HINT)}</p>`})}
      ${u.runtime_available ? "" : '<p role="alert">Local OCR is not installed on this server.</p>'}
      <form data-unlabeled><label class="field">Assignment<select name="assignment_id" required><option value="" disabled ${chosen ? "" : "selected"}>Choose the assignment…</option>${u.assignments.map(a => `<option value="${escape(a.id)}" ${a.id === chosen ? "selected" : ""}>${escape(`${a.code} · ${a.title}`)}</option>`).join("")}</select></label>
      ${run ? sidesReference(item, sides, `<label class="field">Scanned sides per paper<input name="sides" type="number" min="1" max="200" required value="${escape(String(sides))}"></label>`)
        : `<label class="field">Scanned sides per paper<input name="sides" type="number" min="1" max="200" required value="${escape(String(sides))}" aria-describedby="unlabeled-sides-hint"></label>
      <p class="field-hint" id="unlabeled-sides-hint" data-unlabeled-estimate>${length}${escape(estimate)} Count both sides when the papers were scanned two-sided.</p>`}
      <button class="button${run || scanNextStep(job.id, item.overview?.stages || [], "students", escape) ? "" : " primary"}" type="submit">${run ? "Look for first pages again" : "Find first pages"}</button></form></div></details>`;
  }
  // Stamped papers printed for no student (a drafted quiz's unnamed versions, 2026-09-28): the name written on each is read by the
  // worker, which names the copies a confident match settles; the rest are named here, each from its own section's roster. What was
  // read is untrusted text, escaped; names appear only on this page.
  const COPY_STATES = {named: "Named", identified: "Identified", conflict: "Check", needs_review: "Check who wrote it", unread: "Reading the name…", failed: "Name not read"};
  const copyPickers = new Map();
  function copyNamesSection(item) {
    copyPickers.clear();
    const view = item.copy_names;
    if (!view || !view.copies.length) return "";
    const open = view.copies.filter(c => ["needs_review", "conflict", "failed"].includes(c.state));
    const named = view.copies.filter(c => c.state === "named");
    const reading = view.copies.filter(c => ["unread", "identified"].includes(c.state));
    const how = c => c.state === "conflict" || (c.state === "needs_review" && c.conflict) ? c.conflict : c.state === "failed" ? c.error
      : c.match?.enrollment_id ? `Closest: ${c.match.candidates[0]?.name || ""} at ${pct(c.match.confidence)}${c.match.state === "matched" ? " (exact UIN)" : `, below your ${pct(view.threshold)} threshold`}.`
      : c.state === "needs_review" ? "No student of this section is close to what was read." : "";
    const row = c => {
      let choice = c.student ? escape(`${c.student.name} · section ${c.student.section_number}`) : "";
      if (c.state !== "named" && c.state !== "unread") {
        const closest = (c.match?.candidates || []).filter(m => c.students.some(st => st.enrollment_id === m.enrollment_id));
        const picker = studentPicker(escape, {id: `copy-${c.copy_id}`, label: `Student for copy ${c.copy_number} on attachment page ${c.page}`, value: "",
          search: digits => api(`/api/documents/messages/${job.message_id}/copy-names/students`, "POST", {uin: digits, printed_copy_id: c.copy_id})
            .then(r => r.students.map(st => ({value: st.enrollment_id, text: `${st.name} · section ${st.section_number}`, note: `UIN ${pct(st.similarity)}`}))),
          groups: [...(closest.length ? [{label: "Closest to what was read", options: closest.map(m => ({value: m.enrollment_id, text: `${m.name} · section ${m.section_number}`, note: pct(m.confidence)}))}] : []),
            ...[...new Set(c.students.map(st => st.section_number))].map(number => ({label: `Section ${number}`, options: c.students.filter(st => st.section_number === number && !closest.some(m => m.enrollment_id === st.enrollment_id)).map(st => ({value: st.enrollment_id, text: `${st.name} · section ${st.section_number}`}))}))]});
        copyPickers.set(c.copy_id, picker);
        choice = picker.markup;
      }
      return `<tr data-copy-name-row="${escape(c.copy_id)}" data-state="${escape(c.state)}"><td>${c.page}</td><td>${escape(c.title)} · Version ${escape(c.version_label)}<br><small>${escape(placeOf(c.section_number))} · copy ${escape(c.copy_number)}</small></td>
        <td>${firstPageCrop(`/api/documents/messages/${job.message_id}/copy-names/${c.scan_page_id}/name.png`, c.page)}
        <small>${c.state === "unread" ? "Not read yet." : c.state === "failed" ? "" : `Read: ${escape(c.fields.name || "no name")} · UIN ${escape(c.fields.uin || "none")}`}</small></td>
        <td>${choice}</td><td><strong>${escape(COPY_STATES[c.state] || c.state)}</strong>${how(c) ? `<br><small>${escape(how(c))}</small>` : ""}</td></tr>`;
    };
    const table = rows => `<div class="document-version-pages-wrap"><table class="document-version-pages document-unlabeled"><thead><tr><th>Page</th><th>Paper</th><th>First page</th><th>Student</th><th>State</th></tr></thead><tbody>${rows.map(row).join("")}</tbody></table></div>`;
    return `<section class="students-page-group" data-copy-names><h4>Stamped papers printed without a name <span class="scan-detail-count">${view.copies.length}</span></h4>
      <p class="field-hint">Their stamps say which paper and version each is; whose it is comes from the name and UIN written at the top of its first page. An exact roster UIN or a match at your ${pct(view.threshold)} threshold names it by itself; a student who already has a copy of the assignment, and two papers read as one student, wait for you.</p>
      ${open.length ? `<h5>Who wrote these? <span class="scan-detail-count">${open.length}</span></h5>${table(open)}` : reading.length ? "" : '<p class="students-page-empty" role="status"><strong>Every stamped paper has its student.</strong></p>'}
      ${reading.length ? `<p class="field-hint" role="status">Reading the names on ${plural(reading.length, "paper")}…</p>` : ""}
      ${named.length ? `<details class="disclosure"><summary><span>Named (${named.length})</span></summary><div class="disclosure-body">${table(named)}<p class="field-hint">A wrong name is corrected under Change assignment on the print job's copy.</p></div></details>` : ""}</section>`;
  }
  function bindCopyNames(pickers = true) {
    if (!pickers) return;
    for (const [copyId, picker] of copyPickers) picker.bind(host, async value => {
      if (!value) return;
      const input = host.querySelector(`[data-student-picker="${CSS.escape(`copy-${copyId}`)}"] [data-picker-text]`);
      if (input) input.disabled = true;
      try {
        await api(`/api/documents/messages/${job.message_id}/copy-names/assign`, "POST", {printed_copy_id: copyId, enrollment_id: value});
        toast("Named. The paper is this student's from now on.");
        await refresh();
      } catch (cause) { toast(cause.message, true); if (input) input.disabled = false; }
    });
  }
  // The scan's Find students page (papers that need the instructor) and its Matched papers page. Nothing claims the papers are all
  // matched until the run has finished (a stopped run found only some of them).
  const registerHow = () => disclosureMarkup({label: "How registering works", key: "inbox:register-how", body: `<p>${escape(REGISTER_HINT)}</p>`, className: "students-register-how"});
  function studentsPage(item) {
    unlabeledPickers.clear();
    copyPickers.clear();
    const u = item.unlabeled, run = u?.run;
    if (!run) return `${pageHead(item, "students", page === "matched" ? "Matched papers" : "Students")}${page === "matched" ? `<p><a href="${studentsHref}">Back to Students</a></p>` : `${copyNamesSection(item)}${unlabeledSettings(item)}`}`;
    const need = u.covers.filter(needsYou), chosen = u.covers.filter(yourChoice), matched = u.covers.filter(isMatched);
    const status = unlabeledStatus(u), complete = run.status === "complete";
    // When the next step is on another stage (no assignment chosen yet, say), its banner holds the page's one primary (inbox-5).
    const nextStep = scanNextStep(job.id, item.overview?.stages || [], "students", escape);
    const head = title => `<header class="students-page-heading"><h3>${title}</h3>${status ? `<p data-unlabeled-status>${escape(status)}</p>` : ""}${runFacts(run)}</header>${nextStep}${runControls(run, !nextStep)}`;
    const group = (title, covers, hint = "") => covers.length ? `<section class="students-page-group"><h4>${title} <span class="scan-detail-count">${covers.length}</span></h4>${hint ? `<p class="field-hint">${hint}</p>` : ""}${coverTable(covers, u)}</section>` : "";
    const register = nextStep ? registerButton(u).replace("button primary", "button") : registerButton(u);
    if (["pending", "running"].includes(run.status)) return head(page === "matched" ? "Matched papers" : "Students");  // still reading: its progress and Stop
    if (page === "matched") {
      return `${head("Matched papers")}
        <div class="students-page-actions">${register}<a class="button" href="${studentsHref}"><span aria-hidden="true">← </span>Papers that need you (${need.length})</a></div>
        ${matched.length ? "" : `<p class="students-page-empty" role="status"><strong>No matched papers ${complete ? "yet" : "so far"}.</strong></p>`}
        ${group("Identified, not registered yet", matched.filter(c => c.state === "identified"), "Choose another student where the reading is wrong. Register makes them the students' papers.")}
        ${group("Waiting for its label", matched.filter(c => c.state === "registering"))}
        ${group("Registered", matched.filter(c => c.state === "registered"), "Each is its student's paper. A mistake is corrected like any labeled paper, under Change assignment.")}
        ${register ? registerHow() : ""}`;
    }
    // A stopped run leaves Continue as the one primary: Register waits beside it as an ordinary button.
    const registerHere = complete ? register : register.replace("button primary", "button");
    const toMatched = complete || matched.length ? `<a class="button" href="${studentsHref}/matched">Review matched papers (${matched.length})<span aria-hidden="true"> →</span></a>` : "";
    return `${head("Students")}
      ${copyNamesSection(item)}
      ${registerHere || toMatched ? `<div class="students-page-actions">${registerHere}${toMatched}</div>` : ""}
      ${need.length ? group("Who wrote these?", need, "Type a name, a section or the UIN written on the page to find the student, or say it is not a first page.")
        : complete ? `<p class="students-page-empty" role="status"><strong>No paper needs you.</strong> Every first page found is ${chosen.length ? "matched or chosen by you" : "matched"}.</p>` : ""}
      ${group("Chosen by you, not registered yet", chosen)}
      ${register ? `<p class="field-hint">Register takes every identified paper, listed under Matched papers.</p>${registerHow()}` : ""}
      ${unlabeledSettings(item)}`;
  }
  // What each page shows. The stage's words come from the server's overview (escaped: step text is data), the same the Inbox list shows.
  const stageOf = (item, key) => (item.overview?.stages || []).find(stage => stage.key === key);
  // Every stage page opens with its stage's words and, when the next step is on another stage's page, that step and a button to it.
  // `detail: false` leaves the stage's words out where the page says the same thing itself.
  const pageHead = (item, key, title, {detail = true} = {}) => `<header class="students-page-heading"><h3>${title}</h3>${detail && stageOf(item, key)?.detail ? `<p>${escape(stageOf(item, key).detail)}</p>` : ""}</header>${scanNextStep(job.id, item.overview?.stages || [], key, escape)}`;
  // An arrival without a PDF (its graph is one finished Scan stage) has nothing to inspect or retry.
  const noPdf = item => { const stages = item.overview?.stages || []; return stages.length === 1 && stages[0].key === "scan" && stages[0].state === "done" && !(item.pages?.length ?? item.page_count); };
  // A failed inspection says why in a danger callout, with Retry beside it (inbox-7); the reason is the server's words, escaped.
  const INSPECTION_FAILED_TITLE = "Its pages could not be checked";
  const inspection = item => noPdf(item) ? "" : item.decode?.error
    ? calloutMarkup({tone: "danger", title: INSPECTION_FAILED_TITLE, body: item.decode.error, action: {label: "Retry inspection", primary: true, data: {decodeRetry: ""}}})
    : !item.decode ? '<button class="button" type="button" data-decode-retry>Check its pages</button>' : "";
  // What a pop-up can settle one after another: the one-label question, then every bubble sheet that needs review.
  const reviewable = item => ((item.label_modes || []).some(j => j.offer) ? 1 : 0) + (item.recognition?.bubble_sheets?.read?.counts?.needs_review || 0);
  const reviewBubbles = quiet => root.querySelector(".bubble-sheets")?.dispatchEvent(new CustomEvent("review-all", {detail: {quiet}}));
  // The card's way to its next stage, left out (scan-flow.js draws it; found by its data attribute, whatever its markup).
  const withoutNextAction = html => {
    const template = document.createElement("template");
    template.innerHTML = html;
    template.content.querySelectorAll("[data-scan-next-action]").forEach(node => node.remove());
    return template.innerHTML;
  };
  // The scan's own page: the scan as a card, its graph leading to each stage's page, and the one thing to do next.
  function overviewPage(item) {
    const facts = [];
    if (item.grading?.plan) facts.push(`<a href="${escape(item.grading.href)}">Assignment: ${escape(item.grading.plan.title)}</a>`);
    // Where the scan stands is the graph's to say (each stage's label and status) with the footer's "Next · <stage>"; only a scan whose
    // overview has no stages yet says how its inspection stands.
    if (!(item.overview?.stages || []).length) facts.push(`<span data-scan-stage-facts>${escape(`Inspection ${item.decode?.status || "not queued"}`)}</span>`);
    // Work the worker is doing on this scan stays in sight, with how far it has got.
    for (const step of item.overview?.steps || []) if (step.kind === "wait" || step.progress) facts.push(`<span data-scan-working>${escape(step.text)}</span>`);
    // An inspection to start or retry is the card's one action, in place of its way to the Scan stage (inbox-7): Retry is what the
    // instructor came for, and the reason it failed stands above the card.
    const retry = noPdf(item) ? "" : item.decode?.error ? "Retry inspection" : !item.decode ? "Check its pages" : "";
    let card = scanCard({jobId: job.id, overview: item.overview, escape, heading: false, facts,
      actions: retry ? `<button class="button primary" type="button" data-decode-retry>${retry}</button>` : ""});
    if (retry) card = withoutNextAction(card);
    const failed = !noPdf(item) && item.decode?.error ? calloutMarkup({tone: "danger", title: INSPECTION_FAILED_TITLE, body: item.decode.error}) : "";
    return `<div class="document-recognition" data-verdict="${escape(item.recognition?.pages ? item.recognition.verdict : "pending")}">${failed}${card}</div>`;
  }
  // Scan: every page of the scan, what was read on it, and label recovery; sorting by printed version for papers without labels.
  // When no page carries a label (requirement, 2026-09-26), the page leads with where to go next and a look at the first pages; the page-by-page readings, alias recovery and
  // version sorting are folded under their own headings, since for papers printed without labels they are not the way forward.
  // A page's status in plain words (FRONTEND: never a raw code).
  const PAGE_STATUS = {resolved: "label read", manual: "identified by position or by hand", unreadable: "no label read", blank: "blank",
    ambiguous: "two different labels read", malformed: "a label Faculty cannot read", unknown: "a label Faculty never printed",
    cancelled: "label from cancelled paper", superseded: "label from replaced paper", unissued: "label from paper never printed"};
  // A page set aside as part of no paper (requirement, 2026-10-02): it keeps its card, says why, and can be put back.
  const isAside = p => p.status === "unreadable" && p.review_state === "ignored";
  // A written page with no label and no identity, in no grouped paper and no bubble sheet, may be set aside (the server checks again).
  // The Scan page offers it only while the scan's step says its pages without a label belong to no paper (`offer`): before any
  // grouping, a page 2 behind a first-page label would otherwise be one click from holding its student's paper. Papers to check
  // offers it per side, where the grouping shows which pages are in no paper.
  const canAside = p => p.status === "unreadable" && !p.page_stamp_id && p.review_state === "candidate" && !p.packet && !p.bubble_sheet;
  const strayStep = item => (item.overview?.steps || []).some(step => step.action === "set_aside");
  const asideButton = (id, page, file = null) => `<button class="text-button" type="button" data-set-aside-open="${escape(id)}" data-page="${escape(String(page))}"${file ? ` data-file="${escape(String(file))}"` : ""}>Not part of any paper…</button>`;
  // `offer`: the line pointing a page that may be set aside to Papers, where that is done (2026-10-18: one place for it). `look`: the
  // page needs a look, so its label recovery is drawn open.
  const strayLink = item => { const papers = stageOf(item, "papers"); return papers ? `<p class="scan-set-aside-offer" data-set-aside-elsewhere>No student's page? <a href="${escape(stageHref(job.id, papers))}">Set it aside on Papers</a></p>` : ""; };
  // A page that needs a look shows itself, and where else its label was read beside it (requirement, 2026-10-24): open either at full size, or go to the other scan.
  const thumb = (message, id, label, caption) => {
    const src = pageImageUrl(message, id);
    return `<a ${viewLink(`${src}?width=1700`, label, escape)} title="Open ${escape(label)}"><img src="${src}?width=200" alt="${escape(label)}" loading="lazy" width="200"><span>${escape(caption)}</span></a>`;
  };
  const lookPictures = p => {
    const others = p.review_state === "conflict" ? (p.also_scanned || []) : [];
    const when = o => new Date(o.received_at).toLocaleString(undefined, {weekday: "short", hour: "numeric", minute: "2-digit"});
    const why = others.length ? `<p class="field-hint" data-also-scanned>Its label was also read ${others.map(o => `on page ${o.source_page_index} of ${o.message_id === job.message_id ? "this scan" : `the scan received ${escape(when(o))} (<a href="#inbox/job/${escape(o.job_id)}/pages">open it</a>)`}`).join(" and ")}, with different content: the same paper scanned twice, or one label on two papers. Compare them below.</p>` : "";
    return `${why}<figure class="scan-first-look" data-look-pages><div class="scan-first-look-pages">${thumb(job.message_id, p.id, `Attachment page ${p.source_page_index}`, `This page`)}${others.map(o => thumb(o.message_id, o.id, `Page ${o.source_page_index} of the other scan`, `Also scanned · page ${o.source_page_index}`)).join("")}</div></figure>`;
  };
  const pageCard = (p, reasons = new Map(), offer = "", look = false) => `<article class="document-scan-page" data-patch-key="${escape(p.id)}"><h4>Attachment page ${p.source_page_index} · ${escape(PAGE_STATUS[p.status] || p.status)}${isAside(p) ? " · set aside" : p.review_state === "duplicate" ? " · repeated content" : p.review_state === "conflict" ? " · changed content" : ""}</h4>${look ? lookPictures(p) : ""}${p.copy_id ? `<p>${escape(p.code)} · ${escape(p.term === "Other" ? p.term_other : p.term)} ${p.year} · ${escape(placeOf(p.section_number))} · ${escape(p.title)} ${escape(p.version_label)} · Copy ${p.copy_number}, page ${p.page_number}${p.superseded_at ? " · replaced paper" : ""}</p>` : p.packet || isAside(p) ? "" : "<p>Not matched to a page of any paper yet.</p>"}${markCount(p)}${p.packet ? `<p class="field-hint" data-packet-note>Side ${p.packet.side} of ${p.packet.sides} in copy ${p.packet.copy_number}'s grouped paper.</p>` : ""}${p.sequence ? `<p class="field-hint" data-sequence-note>Identified by position: side ${p.sequence.side} of the paper labeled on attachment page ${p.sequence.label_page}.</p>` : ""}${isAside(p) ? `<p class="field-hint" data-set-aside-note>Set aside as part of no paper${reasons.get(p.id) ? `: ${escape(reasons.get(p.id))}` : ""}. <button class="text-button" type="button" data-set-aside-undo="${escape(p.id)}">Undo</button></p>` : ""}<a ${viewLink(`/api/inbox/artifacts/${p.page_artifact_id}/download`, `Attachment page ${p.source_page_index} (PDF)`, escape)}>Page PDF</a> · <a ${viewLink(`/api/inbox/artifacts/${p.raster_artifact_id}/download`, `Attachment page ${p.source_page_index} (image)`, escape)}>Scan image</a>${offer && canAside(p) ? offer : ""}${isAside(p) ? "" : recovery(p, look && !p.page_stamp_id && !p.packet)}</article>`;
  // The pages set aside on this scan, each with its reason and Undo: on the Scan page and on Papers to check.
  const setAsideList = list => list?.length ? `<section class="students-page-group" data-set-aside-list><h4>Set aside <span class="scan-detail-count">${list.length}</span></h4>
    <p class="field-hint">Kept with the scan and in no paper: the scan does not wait on them.</p>
    <ul class="scan-set-aside-list">${list.map(entry => `<li data-set-aside-page="${escape(entry.scan_page_id)}"><a ${viewLink(`${pageImage(entry.scan_page_id)}?width=1700`, `Attachment page ${entry.page}`, escape)}>Page ${entry.page}${entry.files > 1 ? ` of file ${entry.file}` : ""}</a><span>${escape(entry.reason || "No reason recorded")}</span><button class="text-button" type="button" data-set-aside-undo="${escape(entry.scan_page_id)}">Undo</button></li>`).join("")}</ul></section>` : "";
  // Choosing a reason: the usual ones, or the instructor's own words. Returns the markup; `reasonOf(form)` reads the choice.
  const reasonFields = (options, legend) => `<fieldset class="choice-group scan-reason-choices"><legend>${escape(legend)}</legend>${options.map((option, index) => `<label class="checkbox-field"><input type="radio" name="reason_choice" value="${escape(option)}" ${index ? "" : "checked"}>${escape(option)}</label>`).join("")}<label class="checkbox-field"><input type="radio" name="reason_choice" value="">Something else</label></fieldset>
    <label class="field" data-reason-other hidden>In your words<input name="reason_other" maxlength="1000" autocomplete="off"></label>`;
  const bindReason = form => {
    const other = form.querySelector("[data-reason-other]"), text = form.elements.reason_other;
    form.querySelectorAll('input[name="reason_choice"]').forEach(input => { input.onchange = () => {
      other.hidden = input.value !== "" || !input.checked; text.required = !other.hidden; if (!other.hidden) text.focus();
    }; });
  };
  const reasonOf = form => (form.elements.reason_choice.value || form.elements.reason_other.value).trim();
  // "Not part of any paper…": a page with no label that is no student's (a section header sheet, scratch paper), set aside with a
  // reason. It stays with the scan and listed; undo puts it back (requirement, 2026-10-02).
  function openSetAside({id, page, file}) {
    const dialog = document.querySelector("#dialog");
    if (!showDialog || !dialog) return;
    const label = `Attachment page ${page}${file ? ` of file ${file}` : ""}`, requestId = nonce();
    showDialog(`<form id="scan-set-aside" class="scan-set-aside-dialog">${dialogHeader("Not part of any paper?", escape(label))}<div class="dialog-body">
      <p class="section-lead">Set it aside if it is no student's page: it stays with the scan, listed with your reason, and you can undo it.</p>
      <figure class="scan-set-aside-page"><a ${viewLink(`${pageImage(id)}?width=1700`, label, escape)}><img src="${pageImage(id)}?width=300" alt="${escape(label)}" width="150"></a></figure>
      ${reasonFields(ASIDE_REASONS, "Why it is in no paper")}
      <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Set aside</button></footer></form>`);
    const form = dialog.querySelector("#scan-set-aside"), error = form.querySelector(".form-error"), submit = form.querySelector('button[type="submit"]');
    const alive = dialogSession(form, dialog);
    bindReason(form);
    form.onsubmit = async event => {
      event.preventDefault();
      if (submit.disabled) return;
      setBusy(submit, true);
      try {
        await api(`/api/documents/scans/${encodeURIComponent(id)}/set-aside`, "POST", {reason: reasonOf(form), request_id: requestId});
        if (alive()) dialog.close();
        toast(`Page ${page} is set aside: it stays with the scan, listed with your reason.`);
        if (host.isConnected) await refresh();
        // The button that opened the dialog is gone with the redraw: focus goes to this page's Undo in the list instead.
        content.querySelector(`[data-set-aside-list] [data-set-aside-undo="${CSS.escape(id)}"]`)?.focus();
      } catch (cause) { if (!alive()) return toast(cause.message, true); error.textContent = cause.message; error.hidden = false; setBusy(submit, false); }
    };
  }
  async function undoSetAside(button) {
    setBusy(button, true);
    try {
      const result = await api(`/api/documents/scans/${encodeURIComponent(button.dataset.setAsideUndo)}/set-aside`, "POST", {undo: true, request_id: nonce()});
      toast(`Page ${result.page} is back among the pages with no label.`);
      await refresh();
    } catch (cause) { toast(cause.message, true); setBusy(button, false); }
  }
  // OTHER PAPER (requirement, 2026-10-17): the flagged first page beside what it is compared with, the other first pages of this scan read as
  // the same student and each paper of the assignment the student already has, every page of it. Read only; choosing stays in the row.
  async function openOtherPaper(button) {
    const dialog = document.querySelector("#dialog");
    if (!showDialog || !dialog) return;
    const intended = captureDialogIntent(dialog);
    const id = button.dataset.unlabeledOther;
    setBusy(button, true);
    let data;
    try { data = await api(`/api/documents/messages/${job.message_id}/unlabeled/${encodeURIComponent(id)}/other`); }
    catch (cause) { toast(cause.message, true); return; }
    finally { setBusy(button, false); }
    if (!intended() || !host.isConnected) return;
    const cover = pid => `/api/documents/messages/${escape(job.message_id)}/unlabeled/${escape(pid)}/cover.png`;
    const image = pageImageUrl;
    const figure = (src, full, label) => `<figure><a ${viewLink(full, label, escape)}><img src="${src}" alt="${escape(label)}" loading="lazy"></a><figcaption class="field-hint">${escape(label)}</figcaption></figure>`;
    const here = data.here.map(c => figure(cover(c.scan_page_id), cover(c.scan_page_id), `First page on attachment page ${c.page} of this scan`)).join("");
    const papers = data.papers.map(paper => `<section class="unlabeled-other-paper"><h3>${paper.pages.some(pg => pg.message_id === job.message_id) ? "In this scan" : escape(paper.pages[0]?.filename || "Another scan")} · ${plural(paper.pages.length, "page")}</h3>
      <div class="unlabeled-other-pages">${paper.pages.map(pg => figure(`${image(pg.message_id, pg.scan_page_id)}?width=900`, `${image(pg.message_id, pg.scan_page_id)}?width=1700`,
        `Page ${pg.page_number} · attachment page ${pg.attachment_page}${pg.message_id === job.message_id ? "" : pg.filename ? ` of ${pg.filename}` : ""}`)).join("")}</div></section>`).join("");
    showDialog(`<form id="unlabeled-other" class="unlabeled-other-dialog">${dialogHeader(`${escape(data.student || "This student")}'s other paper`, escape(`Compared with the first page on attachment page ${data.this_page}`))}<div class="dialog-body">
      <section class="unlabeled-other-paper"><h3>This first page</h3><div class="unlabeled-other-pages">${figure(cover(id), cover(id), `First page on attachment page ${data.this_page}`)}</div></section>
      ${data.here.length ? `<section class="unlabeled-other-paper"><h3>Also read as ${escape(data.student || "this student")} in this scan</h3><div class="unlabeled-other-pages">${here}</div></section>` : ""}
      ${papers || (data.here.length ? "" : '<p class="field-hint">No other paper of this assignment is on file for this student now.</p>')}
      </div><footer class="dialog-footer"><button type="button" class="button" data-action="close">Close</button></footer></form>`);
  }
  // The first pages of the scan, small (one paper's worth when its length is known): enough to see what the scan holds.
  const firstLook = (item, count) => `<figure class="scan-first-look" data-scan-first-look><div class="scan-first-look-pages">${item.pages.slice(0, count).map(p => `<a ${viewLink(`${pageImage(p.id)}?width=1700`, `Attachment page ${p.source_page_index}`, escape)} title="Open attachment page ${p.source_page_index}"><img src="${pageImage(p.id)}?width=200" alt="Attachment page ${p.source_page_index}" loading="lazy" width="200"><span>Page ${p.source_page_index}</span></a>`).join("")}</div>
    <figcaption class="field-hint">The first ${plural(Math.min(count, item.pages.length), "page")} of the scan${item.pages.length > count ? `, of ${item.pages.length}` : ""}. Open one to see it at full size.</figcaption></figure>`;
  const pageImage = id => pageImageUrl(job.message_id, id);
  // Every Scan page has one layout (inbox-2): the pages that need a look drawn open, then every page folded under "All N pages"
  // (its cards drawn only while it is open: a section's scan is hundreds of them), then the other tools folded. A scan without
  // labels also shows its first pages small. Unreadable pages a later stage settles (Students for papers without labels, Papers
  // for pages not grouped yet, Bubble sheets for a sheet) are that stage's, not a look here; a doubtful label (cancelled, unknown…)
  // is always a look, as the overview's check says. So is a failed label reading that can still be retried (2026-10-06): its Retry
  // is only here, and Home counts it until it is read, recovered by hand or grouped (workspace_overview's alias_ocr queue).
  function scanPage(item) {
    const tally = states => item.pages.filter(p => states.includes(p.status)).length;
    const other = item.pages.length - tally(["resolved", "manual", "unreadable", "blank"]);
    const aside = item.pages.filter(isAside).length, reasons = new Map((item.set_aside || []).map(entry => [entry.scan_page_id, entry.reason]));
    const offer = strayStep(item);
    const summary = `Pages (${item.pages.length}): ${tally(["resolved"])} with a label read, ${tally(["manual"])} identified by position or by hand, ${tally(["unreadable"])} with no label${aside ? ` (${aside} set aside)` : ""}, ${tally(["blank"])} blank${other ? `, ${other} with a label that needs review` : ""}`;
    const unlabeled = item.pages.length > 1 && tally(["unreadable"]) > 1 && !tally(["resolved", "manual"]);
    const grouping = (item.overview?.steps || []).some(step => step.kind === "todo" && step.tool === "exams");
    const elsewhere = unlabeled || grouping;
    const reading = p => !p.page_stamp_id && p.alias_ocr ? p.alias_ocr : null;
    // A page repeated byte for byte from another scan needs nothing (a whole scan uploaded twice would otherwise list every page);
    // one that arrived with different content does, as the overview's check says.
    const settledElsewhere = p => p.bubble_sheet || (elsewhere && p.status === "unreadable");
    // A page set aside needs nothing more: it is listed under Set aside, with its reason.
    const needsLook = p => !isAside(p) && (p.review_state === "conflict" || reading(p)?.result?.outcome === "suggested" || reading(p)?.can_retry
      || (!settledElsewhere(p) && ((!p.packet && !["resolved", "manual", "blank"].includes(p.status))
        || reading(p)?.status === "failed" || ["conflict", "no_match"].includes(reading(p)?.result?.outcome))));
    const attention = item.pages.filter(needsLook);
    const openPages = host.querySelector("[data-scan-all-pages]")?.open ?? false;
    const openOther = host.querySelector("[data-scan-other-options]")?.open ?? ["pending", "running"].includes(item.version_sort?.latest?.status);
    // Sorting by printed version is the way forward when the scan's own step asks for it (nothing printed says what the pages are):
    // then it is drawn on the page, open (2026-10-18); otherwise it stays one of the Other options.
    const sortAsked = (item.overview?.steps || []).some(step => step.kind === "todo" && step.tool === "sort");
    const sort = versionSort(item, sortAsked);
    const head = `${pageHead(item, "scan", "Scan", {detail: !item.decode?.error})}${inspection(item)}${unlabeled ? firstLook(item, item.unlabeled?.paper_sides || 6) : ""}`;
    // Nothing retained yet: what happens next (a failed inspection's callout above already says why and offers Retry).
    if (!item.pages.length) return `${head}${item.decode?.error ? "" : "<p>Pages will appear once the worker has checked the scan's PDF.</p>"}`;
    const strayOffer = offer ? strayLink(item) : "";
    const look = attention.length ? `<section class="students-page-group" data-scan-pages-attention><h4>Pages that need a look <span class="scan-detail-count">${attention.length}</span></h4>
      <div class="scan-page-list">${attention.map(page => pageCard(page, reasons, strayOffer, true)).join("")}</div></section>` : "";
    const options = unlabeled || (sort && !sortAsked) ? `<details class="disclosure scan-page-group" data-scan-other-options ${openOther ? "open" : ""}><summary><span>Other options</span><span class="disclosure-hint">${unlabeled ? "Recover labels, or split pages by printed version" : "Split pages by printed version"}</span></summary>
        <div class="disclosure-body"><p class="field-hint">${unlabeled ? "Labeled paper after all? Open a page under All pages and recover it from the code printed beside its label; to split quizzes into one PDF per printed version, sort them below."
          : "A page's label is recovered on the page itself, from the code printed beside it. To split quizzes printed without labels into one PDF per printed version, sort them below."}</p>
        ${sortAsked ? "" : sort}</div></details>` : "";
    // Pages set aside are listed, with Undo, on Papers (2026-10-18: setting aside lives on one stage); here a line says where.
    const papers = stageOf(item, "papers"), asideCount = (item.set_aside || []).length;
    const asideNote = asideCount ? `<p class="field-hint" data-set-aside-elsewhere>${plural(asideCount, "page")} set aside as part of no paper${papers ? `: <a href="${escape(stageHref(job.id, papers))}">see them on Papers</a>` : ""}.</p>` : "";
    return `${head}
      <p class="field-hint">${escape(summary)}</p>
      ${look}${papers ? asideNote : setAsideList(item.set_aside)}${sortAsked ? sort : ""}
      <details class="disclosure scan-page-group" data-scan-all-pages ${openPages ? "open" : ""}><summary><span>All ${plural(item.pages.length, "page")}</span><span class="disclosure-hint">What was read on each, with its PDF and scan image</span></summary>
        <div class="disclosure-body"><div class="scan-page-list" data-scan-pages>${openPages ? item.pages.map(page => pageCard(page, reasons, strayOffer)).join("") : ""}</div></div></details>
      ${options}`;
  }
  // Assignment: what the labels say the scan is for, how its print jobs are labeled, and the assignment it is filed under.
  function assignmentPage(item) {
    const r = item.recognition || {pages: 0, papers: [], doubtful_labels: {}};
    const term = p => `${escape(p.code)} · ${escape(p.term === "Other" ? p.term_other : p.term)} ${p.year} · Section ${p.section_number}`;
    const who = c => `Copy ${c.copy_number}${c.caption ? ` · ${escape(c.caption)}` : c.enrollment_id ? ` · ${escape(c.enrollment_id)}` : ""}`;
    const doubtful = Object.entries(r.doubtful_labels || {}).map(([state, count]) => `${count} ${escape(state)}`).join(", ");
    const sheets = r.bubble_sheets, written = r.pages - r.blank;
    const source = r.labels_read && r.first_pages_read ? "its labels and the names on its first pages" : !r.labels_read && r.first_pages_read ? "the names and UINs on its first pages" : "its labels";
    const verdict = {recognized: r.sections?.length > 1 ? `Recognized from ${source}: one assignment, sat together by ${sectionList(r.sections)}.` : `Recognized from ${source}.`,
      mixed: "Check before using this scan: its labels do not all point to one current assignment.",
      bubble_sheets: `Recognized as bubble sheets from their positioning marks: ${sheets?.sheets} of ${written} page${written === 1 ? "" : "s"} ${sheets?.sheets === 1 ? "is a" : "are"} ${escape(sheets?.template || "")}${sheets?.sheets === 1 ? "" : " sheets"}.`,
      // Filed under an assignment already, pages without labels are no mystery: they are that assignment's papers, found from
      // their first pages. Only a scan nobody has filed is "not recognized".
      unrecognized: sheets?.pending ? "Not recognized yet: no label was read. Checking its pages for bubble sheets…"
        : item.grading?.plan ? `No label says what these pages are. They are ${escape(item.grading.plan.title)}'s papers, found from their first pages${item.unlabeled?.run ? "." : " on the Students page."}`
        : item.unlabeled?.run ? `First pages ${["pending", "running"].includes(item.unlabeled.run.status) ? "are being" : "were"} read for ${escape(item.unlabeled.run.title)}. Choose that assignment (or another) for this scan.`
        : "Faculty found no label or bubble sheet on these pages, so it cannot tell which assignment they belong to. Nothing is guessed from names or page order."}[r.verdict] || "";
    const unsure = sheets?.unsure ? `<p class="field-hint">${sheets.unsure} more page${sheets.unsure === 1 ? " resembles" : "s resemble"} a bubble sheet, but ${sheets.unsure === 1 ? "its" : "their"} positioning marks could not be located.</p>` : "";
    const papers = r.papers.length ? `<ul class="scan-papers">${r.papers.map(p => `<li><strong>${escape(p.title)}</strong> ${escape(p.version_label)} · ${term(p)}${p.current ? "" : " · <strong>not current paper</strong>"} <span>${p.copies} of ${p.issued} issued paper${p.issued === 1 ? "" : "s"} appear${p.copies === 1 ? "s" : ""} in this scan (${p.pages} labeled page${p.pages === 1 ? "" : "s"}).${p.unseen.length ? "" : " Every issued paper has appeared in a scan."}</span>${p.unseen.length ? `<details class="scan-unseen"><summary>${plural(p.unseen.length, "paper")} not seen in any scan yet</summary><p>${p.unseen.slice(0, 40).map(who).join("; ")}${p.unseen.length > 40 ? `; and ${p.unseen.length - 40} more` : ""}.</p></details>` : ""}</li>`).join("")}</ul>` : "";
    const association = !item.grading ? "" : item.grading.plan
      ? `<p class="scan-association">Assignment: <a href="${escape(item.grading.href)}">${escape(item.grading.plan.title)}</a> · <button class="text-button scan-tool-button" data-scan-grading="associate">Change assignment</button></p>`
      // One primary per region: when the next step is on another stage its callout above holds the primary, so this one is plain.
      : `<p><button class="button${scanNextStep(job.id, item.overview?.stages || [], "assignment", escape) ? "" : " primary"}" type="button" data-scan-grading="associate">Choose assignment</button></p>`;
    // "Filed under X." is said once, by the association line.
    return `${pageHead(item, "assignment", "Assignment", {detail: !item.grading?.plan})}${association}<p class="scan-verdict">${verdict}</p>${unsure}${papers}${labelModes(item)}
      <p class="supporting-copy scan-status">${r.pages} page${r.pages === 1 ? "" : "s"}: ${r.without_label} without a label${sheets?.sheets && r.verdict !== "bubble_sheets" ? ` (${sheets.sheets} of them bubble sheets)` : ""}${r.set_aside ? `, ${r.set_aside} set aside as part of no paper` : ""}, ${r.blank} blank${doubtful ? `, and labels needing review: ${doubtful}` : ""}.</p>`;
  }
  // Bubble sheets: the sheets' own panel mounts below; the pop-ups can be taken one after another.
  function sheetsPage(item) {
    const count = reviewable(item);
    return `${pageHead(item, "sheets", "Bubble sheets")}${count ? `<p><button class="button primary" type="button" data-review-all>Review all (${count})</button></p>` : ""}`;
  }
  // Papers: a print job labeled once per paper is asked about here, before its grouping, as one line with the dialog's two choices
  // (2026-10-18: "One label per paper?" no longer opens by itself). It stays until answered; the details fold under it.
  const labelQuestion = (item, primary = true) => {
    const jobs = (item.label_modes || []).filter(j => j.offer);
    if (!jobs.length) return "";
    const many = jobs.length > 1;
    return `<div class="scan-label-ask" data-label-question data-batches="${escape(jobs.map(j => j.batch_id).join(" "))}"><p><strong>One label per paper?</strong> This scan has one label per paper, but ${many ? "its print jobs expect" : `${jobName(jobs[0])} expects`} a label on every page.
      <button class="button${primary ? " primary" : ""}" type="button" data-label-question-answer="first_page">Use first-page labels only</button>
      <button class="button" type="button" data-label-question-answer="keep">Keep expecting a label on every page</button></p>
      ${disclosureMarkup({label: "What this changes", key: "inbox:label-question", body: `${jobs.map(j => `<p><strong>${jobName(j)}</strong> was issued with a label on each of its ${j.page_count} pages. This scan has ${j.labels} first-page labels${j.sides ? `, each ${j.sides} sides after the one before` : ""}, and no label read on any other page.</p>`).join("")}<p>With first-page labels only, Faculty expects a label on page 1 of each copy and nothing on its other pages, so those pages stop being reported as missing. Every paper stays grouped under its first-page label, and each side of a paper that adds up takes its position as its page. Nothing that was issued or scanned changes, and you can switch back on Assignment.</p>`})}</div>`;
  };
  // A paper accepted with missing pages that has more pages now (labeled on every page): saved again to include them, as the
  // overview's check says (bugs-backend-1). The server counts it by the check's own rule (scans in the Inbox only).
  const latestRevision = a => a.revisions[a.revisions.length - 1] || null;
  const grown = a => Boolean(a.grown);
  const needsReview = a => Boolean(a.missing.length || a.conflicts.length || a.submission_choice_required || grown(a));
  // What grouping on Papers would settle: a paper whose only question is its missing pages (inbox-1). Changed content, a submission
  // to choose or pages that arrived after a partial paper was accepted are Review's whatever Papers does.
  const waitsForGrouping = a => Boolean(a.missing.length && !a.conflicts.length && !a.submission_choice_required && !grown(a));
  // One printed copy's reviewed paper (inbox-10): each page's observations named as the instructor sees them (the scan and
  // attachment page) with a small picture, the page the latest review chose (or the only one there is) already selected. A paper
  // already reviewed with nothing to settle shows its revisions and folds the form under "Edit pages…".
  function assemblyMarkup(a) {
    const accepted = o => ["resolved", "manual"].includes(o.status) && o.review_state !== "rejected";
    const last = latestRevision(a);
    let chosen = new Set();
    try { chosen = new Set(last ? JSON.parse(last.selection_json).map(s => s.scan_page_id) : []); } catch { /* an unreadable revision preselects nothing */ }
    const where = o => `${o.message_id === job.message_id ? "This scan" : o.source_filename || "Another scan"} · attachment page ${o.source_page_index ?? "?"}${o.review_state === "duplicate" ? " · repeated content" : o.review_state === "conflict" ? " · changed content" : ""}`;
    const image = o => pageImageUrl(o.message_id, o.id);
    const field = p => {
      const options = p.observations.filter(o => ["resolved", "manual"].includes(o.status));
      const lone = options.filter(accepted).length === 1 ? options.find(accepted).id : null;
      const picked = options.find(o => chosen.has(o.id))?.id || lone;
      return `<div class="document-assembly-page"><label class="field">Page ${p.page_number}<select name="selection"><option value="">Leave unselected</option>${options.map(o => `<option value="${escape(o.id)}" ${o.id === picked ? "selected" : ""}>${escape(where(o))}</option>`).join("")}</select></label>
        ${options.length ? `<div class="scan-first-look-pages document-assembly-thumbs">${options.map(o => `<a ${viewLink(`${image(o)}?width=1700`, `Page ${p.page_number}: ${where(o)}`, escape)} title="Open ${escape(where(o))}"><img src="${image(o)}?width=200" alt="Page ${p.page_number}: ${escape(where(o))}" loading="lazy" width="120">${options.length > 1 ? `<span>${escape(o.message_id === job.message_id ? `Page ${o.source_page_index ?? "?"}` : `Other scan, page ${o.source_page_index ?? "?"}`)}</span>` : ""}</a>`).join("")}</div>` : ""}</div>`;
    };
    const form = `<form data-assembly="${escape(a.id)}">${a.pages.map(field).join("")}<label><input type="checkbox" name="partial"> Accept it with pages missing</label><label class="field">Review reason<input name="reason" required maxlength="1000"></label><button class="button" type="submit">Save reviewed paper</button></form>`;
    const revisions = a.revisions.map(r => `<div class="document-assembly-revision">Version ${r.revision}${r.partial ? " · pages missing" : ""}${a.selected_submission_revision_id === r.id ? " · the student's paper" : ""} · <a ${viewLink(`/api/documents/assemblies/${r.id}/download`, "Reviewed pages", escape)}>Reviewed pages (PDF)</a>${a.enrollment_id && a.other_reviewed_copies.length ? ` <form class="document-choice" data-choice="${escape(r.id)}"><label class="field">Why this one<input name="reason" required maxlength="1000"></label><button class="button" type="submit">Use this as the student's paper</button></form>` : ""}</div>`).join("");
    const settled = a.revisions.length && !needsReview(a);
    const editOpen = settled && host.querySelector(`[data-assembly-edit="${CSS.escape(a.id)}"]`)?.open;
    return `<section class="document-assembly" data-patch-key="${escape(a.id)}"><h4>${escape(a.title)} · ${escape(a.version_label)} · Copy ${a.copy_number}</h4>
      <p>Pages missing: ${a.missing.join(", ") || "none"} · Pages scanned twice with different content: ${a.conflicts.join(", ") || "none"}</p>
      ${grown(a) ? "<p>Accepted with missing pages, and more of its pages arrived since: save it again to include them.</p>" : ""}
      ${a.submission_choice_required ? "<p>This student has another reviewed paper of this assignment. Choose which one is their paper.</p>" : ""}
      ${revisions}
      ${settled ? `<details class="disclosure" data-assembly-edit="${escape(a.id)}" ${editOpen ? "open" : ""}><summary><span>Edit pages…</span></summary><div class="disclosure-body">${editOpen ? form : ""}</div></details>` : form}</section>`;
  }
  // Review: the assembled papers that need a look first, the rest folded (their forms drawn only while open), and the review that
  // hands the scan to grading. While pages still wait to be grouped on Papers, the papers missing nothing but pages are folded too:
  // grouping fills most of them in, and hand-assembling them first would only race it (inbox-1). A paper with any other question
  // stays open under "Assembled papers to check".
  function reviewPage(item) {
    const needs = item.assemblies.filter(needsReview);
    const rest = item.assemblies.filter(a => !needs.includes(a)), plan = item.grading?.plan;
    const reviewed = Boolean(plan && !item.grading.review_required);
    // What is still open before the review: the earlier stages that are not done, each a link to its page. The server's own check
    // (the "Finish the Inbox checks first" dialog) stays the control; this only says where to look.
    const stages = item.overview?.stages || [], at = stages.findIndex(stage => stage.key === "review");
    const open = (at < 0 ? [] : stages.slice(0, at)).filter(stage => stage.state !== "done");
    // Only the steps for the whole scan (and the worker's own work) hold the review back; flagged papers stay behind on their own.
    const holding = (item.overview?.steps || []).some(step => step.kind === "wait" || (["todo", "check"].includes(step.kind) && step.holds === "scan"));
    const stillOpen = open.length && !holding ? `<p class="scan-still-open" data-scan-still-open>Flagged papers stay in: ${open.map(stage => `<a href="${escape(stageHref(job.id, stage))}">${escape(stage.label)}</a>`).join(", ")}</p>` : "";
    // BLOCKERS YOU CAN ACT ON (2026-10-18): while a step for the whole scan (or the worker's own work) holds the review, Finish is
    // disabled with its reason, each holding step a link to the stage that settles it (the stage whose words carry that step). The
    // server's check stays the control; this only reads the overview.
    const blockers = holding ? (item.overview?.steps || []).filter(step => step.kind === "wait" || (["todo", "check"].includes(step.kind) && step.holds === "scan")).map(step => {
      const stage = stages.find(entry => entry.key !== "review" && entry.detail && entry.detail.includes(step.text));
      return `<li>${stage ? `<a href="${escape(stageHref(job.id, stage))}">${escape(stage.label)}</a>: ` : ""}${escape(step.text)}</li>`;
    }) : [];
    const held = blockers.length ? `<div class="scan-review-held" id="scan-review-held-${escape(job.id)}" data-scan-review-held><p>Waits for the scan checks:</p><ul data-scan-blockers>${blockers.join("")}</ul></div>` : "";
    // While earlier stages are open the page's next-step banner holds the one primary; Finish is secondary then. Without an
    // assignment the stage's words say when the review comes, and the banner where to go.
    const banner = Boolean(scanNextStep(job.id, stages, "review", escape));
    const finish = !item.grading ? "" : !plan ? (banner ? "" : '<p class="field-hint">Choose the assignment first.</p>')
      : item.grading.review_required ? `${stillOpen}<p><button class="button${banner || holding ? "" : " primary"}" type="button" data-scan-grading="review"${held ? ` disabled aria-describedby="scan-review-held-${escape(job.id)}"` : ""}>Finish review &amp; open grading</button></p>${held}<p class="field-hint">The review passes by itself once the scan checks are done. It makes each paper that is ready that student's reviewed paper; flagged papers follow once you settle them.</p>`
      : `<p class="scan-reviewed" data-scan-reviewed>Reviewed · <a class="link-arrow" href="${escape(item.grading.href)}">Open grading</a></p>`;
    const papersStage = stageOf(item, "papers");
    const waitPapers = (item.overview?.steps || []).some(step => step.kind === "todo" && step.tool === "exams");
    const needsOpen = host.querySelector("[data-scan-needs-assemblies]")?.open ?? false;
    const folded = host.querySelector("[data-scan-assemblies]")?.open ?? false;
    const waiting = waitPapers ? needs.filter(waitsForGrouping) : [], check = needs.filter(a => !waiting.includes(a));
    const needsGroup = `${waiting.length ? `<details class="disclosure students-page-group" data-scan-needs-assemblies ${needsOpen ? "open" : ""}><summary><span>Papers missing pages · ${waiting.length}</span><span class="disclosure-hint">Group the pages on Papers first: grouping fills most of them in</span></summary><div class="disclosure-body">${papersStage ? `<p><a class="link-arrow" href="${escape(stageHref(job.id, papersStage))}">Papers</a></p>` : ""}${needsOpen ? waiting.map(assemblyMarkup).join("") : ""}</div></details>` : ""}${check.length
      ? `<section class="students-page-group"><h4>Papers to check <span class="scan-detail-count">${check.length}</span></h4>${check.map(assemblyMarkup).join("")}</section>` : ""}`;
    return `${pageHead(item, "review", "Review", {detail: !reviewed})}${finish}
      ${needsGroup}
      ${rest.length ? `<details class="disclosure students-page-group" data-scan-assemblies ${folded ? "open" : ""}><summary><span>Every other paper · ${rest.length}</span></summary><div class="disclosure-body">${folded ? rest.map(assemblyMarkup).join("") : ""}</div></details>` : ""}`;
  }
  // How each print job on this scan is labeled. Jobs whose other pages were read somewhere are labeled on every page: nothing to choose.
  const jobName = j => `${escape(j.title)} ${escape(j.version_label)} · ${escape(placeOf(j.section_number))}`;
  function labelModes(item) {
    return (item.label_modes || []).filter(j => j.label_mode === "first_page" || j.can_convert).map(j => j.label_mode === "first_page"
      ? `<p data-label-mode="first_page"><strong>${jobName(j)}</strong> is labeled on the first page only: nothing is expected on pages 2–${j.page_count} of its copies. <button class="text-button" type="button" data-label-mode-set="every_page" data-batch="${escape(j.batch_id)}">Expect a label on every page again</button></p>`
      : `<p data-label-mode="every_page"><strong>${jobName(j)}</strong> expects a label on each of its ${j.page_count} pages, and only first-page labels were read. <button class="text-button" type="button" data-label-mode-set="first_page" data-batch="${escape(j.batch_id)}">Use first-page labels only</button></p>`).join("");
  }
  const decideLabelMode = (batchIds, decision) => api(`/api/documents/messages/${job.message_id}/label-mode`, "POST", {batch_ids: batchIds, decision});
  const LABEL_MODE_DONE = {first_page: "Labeled on the first page only: the other pages are no longer expected to carry a label.",
    every_page: "A label is expected on every page again.", keep: "Kept: a label is still expected on every page. This scan will not ask again."};
  // Asked only from Bubble sheets' Review all (an explicit click): paper issued with a label per page, scanned with one label per paper.
  function offerLabelMode(item, thenReview = false) {
    const jobs = (item.label_modes || []).filter(j => j.offer), dialog = document.querySelector("#dialog");
    if (!jobs.length || !showDialog || !dialog || dialog.open) return;
    const many = jobs.length > 1;
    showDialog(`<form id="label-mode-offer">${dialogHeader("One label per paper?", "This scan does not match how its paper was issued.")}<div class="dialog-body">
      ${jobs.map(j => `<p><strong>${jobName(j)}</strong> was issued with a label for each of its ${j.page_count} pages. This scan has one label per paper instead: ${j.labels} first-page labels${j.sides ? `, each ${j.sides} sides after the one before` : ""}, and no label read on any other page.</p>`).join("")}
      <p><strong>Treat ${many ? "these print jobs" : "this print job"} as labeled on the first page only?</strong></p>
      <p>Faculty will then expect a label on page 1 of each copy and nothing on its other pages, so those pages stop being reported as missing. Every paper stays grouped under its first-page label, and each side of a paper that adds up takes its position as its page. Once the scan's Review passes, those papers become the students' reviewed papers. Nothing that was issued or scanned changes, and you can switch back on Assignment.</p>
      <p class="form-error" role="alert" hidden></p></div><footer class="dialog-footer"><button type="button" class="button" data-label-mode-keep>Keep expecting a label on every page</button><button type="submit" class="button primary">Use first-page labels only</button></footer></form>`);
    const form = dialog.querySelector("#label-mode-offer"), error = form.querySelector(".form-error");
    const alive = dialogSession(form, dialog);
    const decide = async decision => {
      const buttons = [...form.querySelectorAll("footer button")];
      if (buttons.some(button => button.disabled)) return;
      buttons.forEach(button => { setBusy(button, true); });
      try {
        await decideLabelMode(jobs.map(j => j.batch_id), decision);
        if (alive()) dialog.close();
        toast(LABEL_MODE_DONE[decision]);
        if (host.isConnected) await refresh();
        if (thenReview && host.isConnected && !host.closest("[hidden]")) reviewBubbles(true);  // Continue only on this scan.
      } catch (cause) { error.textContent = cause.message; error.hidden = false; buttons.forEach(button => { setBusy(button, false); }); }
    };
    form.onsubmit = event => { event.preventDefault(); void decide("first_page"); };
    form.querySelector("[data-label-mode-keep]").onclick = () => void decide("keep");
  }
  // The first pages' pickers, Continue, Stop and Register: on the scan's page and on its Find students pages alike.
  function bindUnlabeled(item, pickers = true) {
    // Pickers are bound once per drawing: a poll that drew nothing new keeps the ones already bound.
    if (pickers) for (const [pageId, picker] of unlabeledPickers) picker.bind(host, async value => {
      const input = host.querySelector(`[data-student-picker="${CSS.escape(`unlabeled-${pageId}`)}"] [data-picker-text]`);
      if (input) input.disabled = true;
      try {
        await api(`/api/documents/messages/${job.message_id}/unlabeled/review`, "POST", {scan_page_id: pageId,
          decision: ["read", "not_cover"].includes(value) ? value : "student", enrollment_id: ["read", "not_cover"].includes(value) ? undefined : value});
        await refresh();
      } catch (cause) { toast(cause.message, true); if (input) input.disabled = false; }
    });
    // Continue a stopped or failed run: the same assignment and sides; the server carries over what was already read.
    host.querySelectorAll("[data-unlabeled-continue]").forEach(button => { button.onclick = async () => {
      const run = item.unlabeled?.run;
      if (!run) return;
      setBusy(button, true);
      try {
        await api(`/api/documents/messages/${job.message_id}/unlabeled`, "POST", {assignment_id: run.assignment_id, sides: run.sides});
        toast(`Continuing. The ${plural(run.pages_read, "page")} already read are kept.`);
        await refresh();
      } catch (cause) { toast(cause.message, true); setBusy(button, false); }
    }; });
    const unlabeledStop = host.querySelector("[data-unlabeled-stop]");
    if (unlabeledStop) unlabeledStop.onclick = async () => {
      setBusy(unlabeledStop, true);
      try {
        await api(`/api/documents/messages/${job.message_id}/unlabeled/cancel`, "POST", {});
        toast("Stopped. The first pages read so far are kept; ask again to look for the rest.");
        await refresh();
      } catch (cause) { toast(cause.message, true); setBusy(unlabeledStop, false); }
    };
    host.querySelectorAll("[data-unlabeled-other]").forEach(button => { button.onclick = () => openOtherPaper(button); });
    const unlabeledRegister = host.querySelector("[data-unlabeled-register]");
    if (unlabeledRegister) unlabeledRegister.onclick = async () => {
      setBusy(unlabeledRegister, true);
      try {
        await api(`/api/documents/messages/${job.message_id}/unlabeled/register`, "POST", {fingerprint: item.unlabeled.fingerprint}, {}, 120000);
        toast("Registration requested. Each paper joins its student once its label is issued, then goes on to grading with the rest of the scan.");
        await refresh();
      } catch (cause) { toast(cause.message, true); setBusy(unlabeledRegister, false); }
    };
  }
  // Papers to check (requirement, 2026-09-24): the papers the grouping holds
  // back and why, sheets scanned back side first that grouping would put in order, and pages in no paper, each with its scanned sides.
  // The one action is the ordinary grouping of exactly this plan (fingerprint-checked by the server); nothing else changes the scan.
  // A held paper's way out, in the order the pages take it: its sides chosen by hand (requirement, 2026-10-02), or what was read kept
  // as a partial paper on Review. While nothing is grouped yet, Review folds the papers grouping would complete (inbox-1), so the
  // papers that add up are grouped first; with nothing to group, the fold is named.
  function heldWay(item, data) {
    const review = `<a href="${escape(stageHref(job.id, stageOf(item, "review") || {page: "review"}))}">Review</a>`;
    const before = (item.overview?.steps || []).some(step => step.kind === "todo" && step.tool === "exams");
    return `Group it by hand under Other ways to group, or accept what was read as a paper with pages missing on ${review}${!before ? "" : data.ready ? " once the papers that add up are grouped" : ", under Papers missing pages"}.`;
  }
  const paperImage = page => pageImageUrl(job.message_id, page.scan_page_id);
  // Papers (2026-10-18, one way to do each job): the label question when the scan asks it, then the one primary path, Group N papers,
  // with what the grouping holds; every other way to group (a held paper's sides chosen by hand, grouping at a number of sides per
  // paper) is folded under one "Other ways to group", open while it is the only way forward. Setting a page aside as part of no paper
  // lives here only: pages in no paper are listed with "Not part of any paper…", the stray sheet before any grouping included.
  function papersPage(item, data) {
    const seq = sequencePanel(item, Boolean(data?.ready));
    data ||= {papers: [], ready: 0, grouped: 0, sides: null, turned: 0, files: 0};
    const where = p => `${p.first_page === p.last_page ? `page ${p.first_page}` : `pages ${p.first_page}–${p.last_page}`}${data.files > 1 ? ` of file ${p.file}` : ""}`;
    const who = p => p.kind === "pages" ? "Pages in no paper" : p.student ? `${escape(p.student)} · section ${escape(p.section_number)}` : `Copy ${p.copy_number}`;
    const note = p => p.turned ? `Its sheet was scanned back side first: its first page, page ${p.pages[0].page}, came after its page 2, page ${p.turned}. Grouping puts them in order.`
      : p.gives_back ? `Its own ${p.sides} sides, once the next sheet is put in order.`
      : `${p.problems.join(" ")}${p.grouped_earlier ? " An earlier run grouped it; check it." : ""}`;
    // One side: its picture (opened full size in the viewer), where it sits, and, for a side in no paper, "Not part of any paper…".
    const side = (page, caption, file) => `<figure class="papers-check-side"><a ${viewLink(`${paperImage(page)}?width=1700`, `Attachment page ${page.page}`, escape)}><img src="${paperImage(page)}" alt="Attachment page ${page.page}" loading="lazy"></a>
      <figcaption>${caption}</figcaption>${page.can_set_aside ? asideButton(page.scan_page_id, page.page, data.files > 1 ? file : null) : ""}</figure>`;
    const scanned = (page, n) => `Side ${n + 1} · page ${page.page}${page.first ? " · first page" : page.status === "blank" ? " · blank" : page.set_aside ? " · set aside" : ""}`;
    const held = data.papers.filter(p => !p.ready && p.kind !== "by_hand"), ready = data.papers.filter(p => p.ready), byHand = data.papers.filter(p => p.kind === "by_hand");
    // A held paper's own row says why it cannot be grouped by hand; its Choose its sides… is under Other ways to group.
    const refused = p => p.choose_refused ? `<p class="field-hint">${escape(p.choose_refused)}</p>` : "";
    const paperItem = p => `<div class="papers-check-item" data-papers-item data-ready="${p.ready}"><h5>${who(p)} <small>${escape(where(p))}</small></h5><p class="field-hint">${escape(note(p))}</p>
        ${p.kind === "paper" && !p.ready ? refused(p) : ""}<div class="papers-check-sides">${p.pages.map((page, n) => side(page, scanned(page, n), p.file)).join("")}</div></div>`;
    // A paper grouped by hand: its sides as ticked, each the page its place makes it, its reason and Undo, and the sides left as scanned.
    // Once it is a reviewed paper its grouping stays (the server refuses the undo): it is changed on Review.
    const review = `<a href="${escape(stageHref(job.id, stageOf(item, "review") || {page: "review"}))}">Review</a>`;
    const handItem = p => `<div class="papers-check-item" data-papers-item data-by-hand="${escape(p.run_id || "")}"><h5>${who(p)} <small>${escape(where(p))}</small></h5>
        <p class="field-hint">Grouped by hand${p.reason ? `: ${escape(p.reason)}` : ""}. ${p.assembled ? `<span data-by-hand-assembled>It is a reviewed paper now: change it on ${review}.</span>` : `<button class="text-button" type="button" data-by-hand-undo="${escape(p.run_id || "")}">Undo</button>`}</p>
        <div class="papers-check-sides">${p.pages.map(page => side(page, `${page.status === "blank" ? "Blank" : `Page ${page.side}`} · attachment page ${page.page}`, p.file)).join("")}</div>
        ${p.left.length ? `<p class="field-hint papers-check-left">Left as scanned, in no paper:</p><div class="papers-check-sides">${p.left.map(page => side(page, `Attachment page ${page.page}${page.status === "blank" ? " · blank" : ""}`, p.file)).join("")}</div>` : ""}</div>`;
    const group = (title, items, hint, markup = paperItem, key = "") => items.length ? `<section class="students-page-group"${key ? ` ${key}` : ""}><h4>${title} <span class="scan-detail-count">${items.length}</span></h4>${hint ? `<p class="field-hint">${hint}</p>` : ""}
      ${items.map(markup).join("")}</section>` : "";
    const button = data.ready ? `<button class="button primary" type="button" data-papers-group>Group ${plural(data.ready, "paper")}</button>` : "";
    // The stage's words and the next step first (GOAL FIRST), then what the grouping holds. "Nothing is left out" only once
    // something was grouped: a scan grouped into nothing has everything left out.
    const leftOut = held.length || ready.length || byHand.some(p => p.left.some(page => page.can_set_aside));
    const facts = `${data.sides ? `${plural(data.sides, "side")} per paper: ${plural(data.grouped, "paper")} grouped${byHand.length ? `, and ${byHand.length} by hand` : ""}.` : ""}${data.turned ? ` ${plural(data.turned, "sheet")} ${data.turned === 1 ? "looks" : "look"} scanned back side first.` : ""}${!leftOut && data.grouped + byHand.length > 0 ? " Nothing is left out of the grouping." : ""}`.trim();
    const papersHeld = held.some(p => p.kind === "paper"), pagesHeld = held.some(p => p.kind === "pages");
    // A stray sheet before any grouping (the scan's set-aside step) is in no list Papers' plan draws when the scan has no side count:
    // its pages are listed here from the scan's own page rows, each with "Not part of any paper…" (the server checks again).
    const offered = new Set(data.papers.flatMap(p => [...p.pages, ...(p.left || [])]).filter(page => page.can_set_aside).map(page => page.scan_page_id));
    const strays = strayStep(item) ? (item.pages || []).filter(p => canAside(p) && !offered.has(p.id)) : [];
    const strayGroup = strays.length ? `<section class="students-page-group" data-papers-strays><h4>Pages in no paper <span class="scan-detail-count">${strays.length}</span></h4>
      <p class="field-hint">They carry no label and are in no paper. A page that is no student's can be set aside; a student's page is recovered from the code printed beside its label on <a href="${escape(stageHref(job.id, stageOf(item, "scan") || {page: "pages"}))}">Scan</a>.</p>
      <div class="papers-check-item" data-papers-item><div class="papers-check-sides">${strays.map(p => side({scan_page_id: p.id, page: p.source_page_index, status: p.status, can_set_aside: true}, `Attachment page ${p.source_page_index}`, null)).join("")}</div></div></section>` : "";
    // Every other way to group, under one fold: a held paper's sides chosen by hand, and grouping at a number of sides per paper.
    const choosable = held.filter(p => p.kind === "paper" && p.choose);
    const byHandWay = choosable.length ? `<section class="scan-other-way" data-papers-choose-list><h5>Choose a held paper's sides</h5><p class="field-hint">Tick the sides that are its pages, in scan order.</p>
      <ul class="scan-other-way-list">${choosable.map(p => `<li>${who(p)} <small>${escape(where(p))}</small> <button class="button" type="button" data-papers-choose="${escape(p.pages.find(page => page.first)?.scan_page_id || "")}">Choose its sides…</button></li>`).join("")}</ul></section>` : "";
    const otherOpen = host.querySelector("[data-papers-other-ways]")?.open ?? (!!sequencePlan || (!data.ready && (choosable.length > 0 || !(item.sequence?.runs || []).some(run => !run.undone_at))));
    const otherWays = byHandWay || seq.manual ? `<details class="disclosure students-page-group" data-papers-other-ways ${otherOpen ? "open" : ""}><summary><span>Other ways to group</span><span class="disclosure-hint">${[choosable.length ? "choose a held paper's sides" : "", seq.manual ? "group at a number of sides per paper" : ""].filter(Boolean).join(", or ")}</span></summary>
      <div class="disclosure-body">${byHandWay}${seq.manual}</div></details>` : "";
    return `${pageHead(item, "papers", "Papers")}${labelQuestion(item, !button)}${facts ? `<p class="papers-check-facts" data-papers-facts>${facts}</p>` : ""}
      ${button ? `<div class="students-page-actions">${button}</div><p class="field-hint">Groups ${data.ready === ready.length ? "the papers below" : `these and every other paper that now adds up (${plural(data.ready, "paper")} in all)`} exactly as shown, turning sheets scanned back side first; it can be undone.</p>` : ""}
      ${group("Ready to group", ready, "The two faces of one sheet belong to one paper, so a first page on the back of its sheet takes the front as its page 2.")}
      ${group("Held for you", held, `Nothing guesses here: these pages stay as scanned.${papersHeld ? ` ${heldWay(item, data)}` : ""}${pagesHeld ? " A page that is no student's can be set aside." : ""} Open a page to see it at full size.`)}
      ${strayGroup}
      ${group("Grouped by hand", byHand, "Each ticked side is the page its place makes it; until the paper is reviewed, undo puts it back as held.", handItem, "data-papers-by-hand")}
      ${setAsideList(data.set_aside || item.set_aside)}
      ${seq.records}${otherWays}`;
  }
  // "Choose its sides…" on a held paper (requirement, 2026-10-02): each scanned side in
  // order with a tick, the first page ticked for good; the n-th ticked side becomes page n of the copy its label names. Sent with
  // the fingerprint of the sides shown, so a paper that changed meanwhile is refused (409) rather than grouped from an old look.
  function openChooseSides(paper, data) {
    const dialog = document.querySelector("#dialog");
    if (!showDialog || !dialog || !paper?.choose) return;
    const copy = paper.choose.copy_number, issued = paper.choose.pages_issued, requestId = nonce();
    const name = paper.student ? `${paper.student} · section ${paper.section_number} · copy ${copy}` : `Copy ${copy}`;
    const span = `${paper.first_page === paper.last_page ? `Page ${paper.first_page}` : `Pages ${paper.first_page}–${paper.last_page}`}${data.files > 1 ? ` of file ${paper.file}` : ""}`;
    const first = paper.pages.find(page => page.first), byId = new Map(paper.pages.map(page => [page.scan_page_id, page]));
    // "Side" is its place in the scan and "attachment page" its page of the PDF; the page it becomes is said apart, under it. A side
    // set aside as part of no paper cannot be ticked (undo that first, under Set aside).
    const sides = paper.pages.map((page, n) => `<label class="papers-choose-side" data-choose-side data-first="${page.first}"${page.set_aside ? " data-set-aside" : ""}>
        <img src="${paperImage(page)}?width=300" alt="Attachment page ${page.page}" loading="lazy">
        <span class="checkbox-field"><input type="checkbox" name="side" value="${escape(page.scan_page_id)}" ${page.first ? "checked disabled" : page.set_aside ? "disabled" : ""}>Side ${n + 1} · attachment page ${page.page}${page.first ? " · first page" : page.status === "blank" ? " · blank" : page.set_aside ? " · set aside" : ""}</span>
        <span class="papers-choose-page" data-choose-page></span></label>`).join("");
    showDialog(`<form id="papers-choose" class="papers-choose-dialog">${dialogHeader(`Choose the sides of ${escape(name)}`, escape(`${span} · ${paper.problems[0] || ""}`))}<div class="dialog-body">
      <p class="section-lead">Tick the sides that are its pages, in scan order: the first ticked side is page 1, the next page 2, and so on.</p>
      <p class="field-hint" data-choose-summary aria-live="polite"></p>
      <div class="papers-choose-sides">${sides}</div>
      ${reasonFields(HELD_REASONS, "Why it is grouped by hand")}
      ${disclosureMarkup({label: "What grouping by hand does", key: "inbox:held-by-hand", body: `<p>The sides you tick become this copy's pages in that order; a blank side you tick keeps its place and takes no identity, and a side carrying its own label must be that page. The sides you leave unticked stay as scanned, in no paper: set them aside once they are grouped. Nothing is guessed, and it can be undone under Grouped by hand.</p>`})}
      <p class="form-error" role="alert" hidden></p></div>
      <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary" data-choose-group>Group as Copy ${copy}</button></footer></form>`);
    const form = dialog.querySelector("#papers-choose"), error = form.querySelector(".form-error"), submit = form.querySelector("[data-choose-group]");
    const summary = form.querySelector("[data-choose-summary]"), alive = dialogSession(form, dialog);
    bindReason(form);
    const ticked = () => [first.scan_page_id, ...[...form.querySelectorAll('input[name="side"]:checked:not(:disabled)')].map(input => input.value)];
    // The n-th ticked side is page n; a blank one keeps its place and becomes no page, so only written sides count against the
    // pages issued (a duplex back past the last page is fine, as in any grouping).
    const sync = () => {
      const chosen = ticked(), blank = id => byId.get(id)?.status === "blank";
      form.querySelectorAll("[data-choose-side]").forEach(label => {
        const id = label.querySelector("input").value, at = chosen.indexOf(id);
        label.dataset.chosen = String(at >= 0);
        label.querySelector("[data-choose-page]").textContent = at < 0 ? (byId.get(id)?.set_aside ? "Set aside" : "Left as scanned") : blank(id) ? "Blank · no page" : `Page ${at + 1}`;
      });
      const written = chosen.map((id, at) => blank(id) ? 0 : at + 1).filter(Boolean), blanks = chosen.length - written.length;
      const last = written.at(-1), over = issued !== null && last > issued ? last - issued : 0;
      const pages = written.length === 1 ? "page 1" : written.every((number, at) => number === at + 1) ? `pages 1–${last}` : `pages ${written.slice(0, -1).join(", ")} and ${last}`;
      summary.textContent = `${plural(chosen.length, "side")} ticked: ${pages} of copy ${copy}${blanks ? `, and ${plural(blanks, "blank side")}` : ""}.${issued !== null ? ` It was issued ${plural(issued, "page")}.` : ""}${data.sides ? ` Papers here have ${plural(data.sides, "side")}.` : ""}${over ? ` Untick ${plural(over, "side")}: page ${last} is past the pages issued.` : chosen.length < 2 ? " Tick at least one more side." : ""}`;
      submit.disabled = over > 0 || chosen.length < 2;
    };
    form.querySelectorAll('input[name="side"]').forEach(input => { input.onchange = sync; });
    sync();
    form.onsubmit = async event => {
      event.preventDefault();
      if (submit.disabled) return;
      setBusy(submit, true);
      try {
        const result = await api(`/api/documents/messages/${job.message_id}/papers/held`, "POST", {first_scan_page_id: first.scan_page_id, scan_page_ids: ticked(),
          fingerprint: paper.choose.fingerprint, reason: reasonOf(form), request_id: requestId}, {}, 120000);
        if (alive()) dialog.close();
        toast(`Copy ${result.copy_number} is grouped by hand: ${plural(result.pages, "page")}. Undo it under Grouped by hand.`);
        if (host.isConnected) await refresh();
        content.querySelector(`[data-by-hand-undo="${CSS.escape(result.run_id)}"]`)?.focus();  // its opener left with the redraw
      } catch (cause) { if (!alive()) return toast(cause.message, true); error.textContent = cause.message; error.hidden = false; setBusy(submit, false); sync(); }
    };
  }
  // Which stage each page belongs to: its place in the graph on top of the page.
  const PAGE_STAGE = {pages: "scan", assignment: "assignment", students: "students", matched: "students", sheets: "sheets", papers: "papers", review: "review"};
  const STAGE_NAMES = {scan: "Scan", assignment: "Assignment", students: "Students", sheets: "Bubble sheets", papers: "Papers", review: "Review"};
  const missingStage = item => {
    const inspecting = ["pending", "running"].includes(item.overview?.inspection);
    return `<p class="scan-stage-missing" data-scan-stage-missing>This scan has no ${escape(STAGE_NAMES[PAGE_STAGE[page]] || "such")} stage${inspecting ? " yet: its pages are still being checked" : ""}. <a class="link-arrow" href="#inbox/job/${escape(job.id)}">Open the scan</a></p>`;
  };
  let poll = null, sheetPolls = 0;
  // The page's own look every 5 s while the worker is busy with the scan waits while the tab is hidden, and looks once when the tab
  // is shown again. `wake` is that wait's listener, removed when it fires and whenever the page draws again.
  let wake = null;
  const stopWaking = () => { if (wake) document.removeEventListener("visibilitychange", wake); wake = null; };
  // A look that fails says so beside the page's "Updated" time and tries again in 15 s, instead of stopping for good (bugs-frontend-4);
  // the next good read writes the time again. Nothing is read while the Inbox itself is hidden behind another page (bugs-frontend-6):
  // the page is still connected then, so it waits, and coming back to the Inbox redraws it at once.
  const lookFailed = () => {
    if (!host.isConnected) return;
    const node = root.querySelector("[data-scan-updated]");
    if (node) { node.hidden = false; node.textContent = `Couldn't refresh at ${new Date().toLocaleTimeString(undefined, {hour: "2-digit", minute: "2-digit", second: "2-digit"})} · trying again`; }
    clearTimeout(poll);
    poll = setTimeout(lookAgain, 15000);
  };
  function lookAgain() {
    if (!host.isConnected) { stopWaking(); return; }
    if (host.closest("[hidden]")) { clearTimeout(poll); poll = setTimeout(lookAgain, 5000); return; }
    if (!document.hidden) return void refresh().catch(lookFailed);
    wake = () => { if (document.hidden) return; stopWaking(); if (host.isConnected) refresh().catch(lookFailed); };
    document.addEventListener("visibilitychange", wake);
  }
  // One refresh at a time (the Inbox's poll, this page's own poll and every action ask for one): a request made while one is under
  // way runs once more after it, so an older answer is never drawn over a newer one.
  let drawing = null, queued = null;
  function refresh() {
    if (!drawing) return (drawing = draw().finally(() => { drawing = null; }));
    return (queued ||= drawing.catch(() => {}).then(() => { queued = null; return refresh(); }));
  }
  // What was last drawn: a poll that changed nothing touches nothing, and one that changed a count changes only that count
  // (requirement, 2026-09-26). Student pickers carry listeners of their own, so a
  // patch replaces them whole and binds the new ones; everything else is bound by property and simply bound again.
  let drawn = {flow: null, content: null};
  // Only the Scan and Review pages show every page row and assembled paper; the others read the scan's summary. Both are read
  // conditionally: an unchanged scan comes back as a 304 and is drawn from the answer kept for it.
  // Papers reads the page rows too: a stray sheet before any grouping is set aside there, listed from them (2026-10-18).
  const scanPath = `/api/documents/messages/${job.message_id}${["pages", "review", "papers"].includes(page) ? "" : "?view=summary"}`;
  async function draw() {
    if (!host.isConnected || host.closest("[hidden]")) return;
    const [{data: item}, check] = await Promise.all([api.revalidate(scanPath),
      page === "papers" ? api(`/api/documents/messages/${job.message_id}/papers-to-check`) : null]);
    if (!host.isConnected || host.closest("[hidden]")) return;
    try { onItem?.(item); } catch { /* the header is a convenience; the page draws regardless */ }
    // A stage page this scan's graph does not have (a bubble-sheet scan has no Students, a no-PDF arrival only its Scan stage) says
    // so in one line instead of drawing an empty page. The Bubble sheets page is the exception: its reader is the only place a read
    // is requested, and the stage appears only once detection recognized a sheet, so a scan whose sheets were not recognized (or
    // only resemble one) must still be readable there, and any read already made on it reviewable.
    const keys = (item.overview?.stages || []).map(stage => stage.key), missing = Boolean(page && page !== "sheets" && keys.length && !keys.includes(PAGE_STAGE[page]));
    host.querySelector("[data-bubble-slot]")?.toggleAttribute("hidden", missing);
    const markup = missing ? missingStage(item) : !page ? overviewPage(item) : page === "pages" ? scanPage(item) : page === "assignment" ? assignmentPage(item)
      : page === "sheets" ? sheetsPage(item) : page === "papers" ? papersPage(item, check)
      : page === "review" ? reviewPage(item) : studentsPage(item);
    const flowMarkup = flow ? scanFlowNav(job.id, item.overview?.stages || [], PAGE_STAGE[page], escape) : null;
    if (flow && flowMarkup !== drawn.flow) { patchChildren(flow, flowMarkup); drawn.flow = flowMarkup; }
    const changed = markup !== drawn.content;
    const restoreTyping = changed ? keepPickerTyping(content) : () => {};  // What is being typed in a student picker survives the redraw.
    if (changed) { patchChildren(content, markup, "[data-student-picker]"); drawn.content = markup; }
    // Folds that draw what they hold only while open (every page, every other assembled paper, a paper's form): opening one draws
    // it from the scan already read (a 304), closing one lets it go.
    content.querySelectorAll("[data-scan-all-pages], [data-scan-assemblies], [data-scan-needs-assemblies], [data-assembly-edit]").forEach(fold => {
      fold.ontoggle = () => { drawn.content = null; refresh().catch(() => {}); };
    });
    revealCurrentStage(host);
    // One primary on the Bubble sheets page: while a next-step banner leads elsewhere, the reader's first "Read bubble sheets" is secondary.
    host.querySelector("[data-bubble-slot] [data-bubble-first-read]")?.classList.toggle("primary", !content.querySelector("[data-scan-next-step]"));
    bindScanGrading(content, item, {api, escape, toast, showDialog, dialogHeader, jobId: job.id}, refresh);
    const association = JSON.stringify([item.grading?.plan?.id, item.grading?.plan?.section_ids]);
    if (gradingAssociation !== association) {
      root.querySelectorAll(".bubble-sheets").forEach(node => node.dispatchEvent(new CustomEvent("scan-association-changed")));
      // ASSIGNMENT-WORKSPACE: a successful association can change while this scan's
      // address stays put, including an automatic association observed on refresh.
      host.dispatchEvent(new CustomEvent("faculty:scan-association-changed", {bubbles: true,
        detail: {jobId: job.id, planId: item.grading?.plan?.id || null}}));
    }
    gradingAssociation = association;
    const group = content.querySelector("[data-papers-group]");
    if (group) group.onclick = async () => {
      setBusy(group, true);
      try {
        await api(`/api/documents/messages/${job.message_id}/sequence`, "POST", {sides: check.sides, fingerprint: check.fingerprint}, {}, 120000);
        toast(`Grouped ${plural(check.ready, "paper")}. They go on to grading with the rest of the scan.`);
        await refresh();
      } catch (cause) { toast(cause.message, true); setBusy(group, false); }
    };
    // A page in no paper set aside, or put back (requirement, 2026-10-02); a held paper's sides chosen by hand, or that undone.
    content.querySelectorAll("[data-set-aside-open]").forEach(button => {
      button.onclick = () => openSetAside({id: button.dataset.setAsideOpen, page: button.dataset.page, file: button.dataset.file});
    });
    content.querySelectorAll("[data-set-aside-undo]").forEach(button => { button.onclick = () => undoSetAside(button); });
    content.querySelectorAll("[data-papers-choose]").forEach(button => {
      button.onclick = () => openChooseSides(check?.papers.find(paper => paper.choose && paper.pages.some(side => side.first && side.scan_page_id === button.dataset.papersChoose)), check);
    });
    content.querySelectorAll("[data-by-hand-undo]").forEach(button => { button.onclick = async () => {
      setBusy(button, true);
      try {
        const result = await api(`/api/documents/messages/${job.message_id}/sequence/${encodeURIComponent(button.dataset.byHandUndo)}/undo`, "POST", {});
        // Pages corrected since, or repeated by a later scan, keep their identity (the run's undo says which): said, never hidden.
        const kept = (result.sequence?.runs || []).find(run => run.id === button.dataset.byHandUndo)?.undo?.kept || [];
        toast(kept.length ? `Undone: the paper is held for you again. ${plural(kept.length, "page")} kept ${kept.length === 1 ? "its" : "their"} identity (${[...new Set(kept.map(entry => entry.reason))].join("; ")}): attachment page${kept.length === 1 ? "" : "s"} ${kept.map(entry => entry.page).join(", ")}.`
          : "Undone: the paper is held for you again, its pages as scanned.");
        await refresh();
      } catch (cause) { toast(cause.message, true); setBusy(button, false); }
    }; });
    content.querySelectorAll("[data-label-mode-set]").forEach(button => { button.onclick = async () => {
      setBusy(button, true);
      try { await decideLabelMode([button.dataset.batch], button.dataset.labelModeSet); toast(LABEL_MODE_DONE[button.dataset.labelModeSet]); await refresh(); }
      catch (cause) { toast(cause.message, true); setBusy(button, false); }
    }; });
    const reviewAll = content.querySelector("[data-review-all]");
    if (reviewAll) reviewAll.onclick = () => { if ((item.label_modes || []).some(j => j.offer)) offerLabelMode(item, true); else reviewBubbles(false); };
    // The one-label question on Papers, answered in place (2026-10-18: it no longer opens a dialog by itself when the scan is opened).
    content.querySelectorAll("[data-label-question-answer]").forEach(button => { button.onclick = async () => {
      const buttons = [...content.querySelectorAll("[data-label-question-answer]")];
      if (buttons.some(other => other.disabled)) return;
      const decision = button.dataset.labelQuestionAnswer, batches = button.closest("[data-label-question]").dataset.batches.split(" ").filter(Boolean);
      buttons.forEach(other => { setBusy(other, true); });
      try { await decideLabelMode(batches, decision); toast(LABEL_MODE_DONE[decision]); await refresh(); }
      catch (cause) { toast(cause.message, true); buttons.forEach(other => { setBusy(other, false); }); }
    }; });
    clearTimeout(poll);
    stopWaking();
    // Pages are checked for bubble sheets right after inspection; older scans are caught up by the worker, so look again for a while.
    const checkingSheets = item.decode?.status === "complete" && item.recognition?.bubble_sheets?.pending > 0 && sheetPolls++ < 40;
    const unlabeledBusy = ["pending", "running"].includes(item.unlabeled?.run?.status) || (item.unlabeled?.counts?.registering || 0) > 0
      || (item.copy_names?.counts?.unread || 0) > 0;
    if (["pending", "running"].includes(item.decode?.status) || ["pending", "running"].includes(item.version_sort?.latest?.status) || unlabeledBusy || checkingSheets || (!item.grading?.plan && item.recognition?.verdict === "recognized" && sheetPolls++ < 40)) {
      poll = setTimeout(lookAgain, 5000);
    }
    const sortForm = content.querySelector("form[data-version-sort]");
    if (sortForm) {
      const settings = content.querySelector("[data-version-sort-settings]"), pages = content.querySelector("[data-version-sort-pages]"), sortPanel = content.querySelector("[data-version-sort-panel]");
      const remember = () => sortViewState.set(job.message_id, {
        preset: sortForm.elements.preset.value, band: sortForm.elements.band.value, pattern: sortForm.elements.pattern.value,
        pages_per_quiz: sortForm.elements.pages_per_quiz.value, auto_rotate: sortForm.elements.auto_rotate.checked,
        openSettings: settings.open, openPages: pages?.open ?? false, openPanel: sortPanel.open});
      sortForm.oninput = remember;
      sortPanel.ontoggle = remember;
      settings.ontoggle = remember;
      if (pages) pages.ontoggle = remember;
      sortForm.elements.preset.onchange = () => { sortForm.querySelector("[data-custom-pattern]").hidden = sortForm.elements.preset.value !== "custom"; remember(); };
    }
    const unlabeledForm = content.querySelector("form[data-unlabeled]"), unlabeledSettingsNode = content.querySelector("[data-unlabeled-settings]");
    if (unlabeledSettingsNode) {
      const remember = () => unlabeledViewState.set(job.message_id, {open: unlabeledSettingsNode.open,
        assignment_id: unlabeledForm?.elements.assignment_id.value, sides: unlabeledForm?.elements.sides.value});
      unlabeledSettingsNode.ontoggle = remember;
      if (unlabeledForm) unlabeledForm.oninput = remember;
    }
    bindUnlabeled(item, changed);
    bindCopyNames(changed);
    mountCropZoom(content);
    restoreTyping();
    const sortRetry = content.querySelector("[data-version-sort-retry]");
    if (sortRetry) sortRetry.onclick = async () => {
      setBusy(sortRetry, true);
      try { await api(`/api/documents/messages/${job.message_id}/version-sort/retry`, "POST", {}); await refresh(); }
      catch (cause) { toast(cause.message, true); setBusy(sortRetry, false); }
    };
    const retry = content.querySelector("[data-decode-retry]");
    if (retry) retry.onclick = async () => {
      if (retry.disabled) return;
      setBusy(retry, true);
      try { await api(`/api/documents/messages/${job.message_id}/retry`, "POST", {}); toast(item.decode ? "Inspection queued again." : "Inspection queued."); await refresh(); }
      catch (cause) { toast(cause.message, true); setBusy(retry, false); }
    };
    content.querySelectorAll("[data-alias-ocr-retry]").forEach(button => { button.onclick = async () => {
      setBusy(button, true);
      try { await api(`/api/documents/scans/${button.dataset.aliasOcrRetry}/alias-ocr/retry`, "POST", {}); await refresh(); }
      catch (cause) { toast(cause.message, true); setBusy(button, false); }
    }; });
    // Only this page's own forms: the bubble sheet panel binds its forms itself.
    content.querySelectorAll("form").forEach(form => { if (form.matches("[data-sequence]")) return; form.onsubmit = async event => {
      event.preventDefault(); const button = form.querySelector("button"); if (button.disabled) return; setBusy(button, true);
      try {
        const data = new FormData(form);
        if (form.dataset.recover) await api(`/api/documents/scans/${form.dataset.recover}/recover`, "POST", {alias:data.get("alias"), reason:data.get("reason")});
        if (form.dataset.assembly) await api(`/api/documents/copies/${form.dataset.assembly}/assembly`, "POST", {selections:data.getAll("selection").filter(Boolean), partial:data.has("partial"), reason:data.get("reason")});
        if (form.dataset.choice) await api(`/api/documents/assemblies/${form.dataset.choice}/choose`, "POST", {reason:data.get("reason")});
        if ("versionSort" in form.dataset) await api(`/api/documents/messages/${job.message_id}/version-sort`, "POST", {preset:data.get("preset"), pattern:data.get("pattern") || undefined, pages_per_quiz:Number(data.get("pages_per_quiz")), band:data.get("band"), auto_rotate:data.has("auto_rotate")});
        if ("unlabeled" in form.dataset) await api(`/api/documents/messages/${job.message_id}/unlabeled`, "POST", {assignment_id: data.get("assignment_id"), sides: Number(data.get("sides"))});
        await refresh();
      } catch(cause) { toast(cause.message, true); setBusy(button, false); }
    }; });
    // First-page labels: preview first; only the exact previewed plan can be applied.
    const sequenceForm = content.querySelector("form[data-sequence]");
    if (sequenceForm) {
      const planHost = content.querySelector("[data-sequence-plan]"), base = `/api/documents/messages/${job.message_id}/sequence`;
      const bindApply = () => {
        const apply = planHost.querySelector("[data-sequence-apply]");
        if (apply) apply.onclick = async () => {
          const plan = sequencePlan;
          if (!plan) return;
          setBusy(apply, true);
          try {
            await api(base, "POST", {sides: plan.sides, fingerprint: plan.fingerprint}, {}, 120000);
            toast(`Grouped ${plan.counts.grouped} paper${plan.counts.grouped === 1 ? "" : "s"}${plan.counts.identified ? ` and identified ${plan.counts.identified} page${plan.counts.identified === 1 ? "" : "s"}; each counts as the student's paper once the scan's Review passes` : ""}.`);
            if (sequencePlan === plan) sequencePlan = null;
            await refresh();
          } catch (cause) { toast(cause.message, true); setBusy(apply, false); }
        };
      };
      sequenceForm.elements.sides.oninput = () => { sequencePreviewGeneration++; sequencePlan = null; planHost.innerHTML = ""; };
      sequenceForm.onsubmit = async event => {
        event.preventDefault();
        const button = sequenceForm.querySelector("button"); if (button.disabled) return; setBusy(button, true);
        const run = ++sequencePreviewGeneration, sides = sequenceForm.elements.sides.value;
        try {
          const result = await api(`${base}/preview`, "POST", {sides: Number(sides)}, {}, 120000);
          if (run !== sequencePreviewGeneration || !sequenceForm.isConnected || sequenceForm.elements.sides.value !== sides) return;
          sequencePlan = result;
          planHost.innerHTML = sequencePlanMarkup(Boolean(content.querySelector("[data-papers-group]"))); bindApply();
        } catch (cause) { if (run === sequencePreviewGeneration && sequenceForm.isConnected) { sequencePlan = null; planHost.innerHTML = ""; toast(cause.message, true); } }
        finally { setBusy(button, false); }
      };
      bindApply();
      content.querySelectorAll("[data-sequence-undo]").forEach(button => { button.onclick = async () => {
        setBusy(button, true);
        try { await api(`${base}/${button.dataset.sequenceUndo}/undo`, "POST", {}); sequencePlan = null; await refresh(); toast("That run was undone: its papers are ungrouped, and its page identities cleared except in reviewed papers."); }
        catch (cause) { toast(cause.message, true); setBusy(button, false); }
      }; });
    }
  }
  // A failed first read keeps the page's frame (the Inbox's later reads draw into it) and says so where the page would be, with
  // Try again, while the page's own look keeps trying (missed: a bare message in place of the page that nothing ever replaced).
  try { await refresh(); } catch (cause) {
    if (!host.isConnected || drawn.content !== null) return;
    content.innerHTML = calloutMarkup({tone: "danger", title: "This scan's page could not be read", body: cause.message, action: {label: "Try again", primary: true, data: {scanRetry: ""}}});
    const again = content.querySelector("[data-scan-retry]");
    if (again) again.onclick = () => { setBusy(again, true); refresh().catch(() => { if (again.isConnected) setBusy(again, false); }); };
    clearTimeout(poll);
    poll = setTimeout(lookAgain, 15000);
  }
}
