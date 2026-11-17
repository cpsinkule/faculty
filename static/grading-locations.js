import {mountRegions} from "./grading-regions.js";
import {inPrintedOrder, versionTitle} from "./grading-guide.js";
import {filterChipsMarkup, bindFilterChips, whenVisible} from "./ui.js";

// The answer areas' poll (mountLocationChecks): a look at the local work, and the longest wait for a read of the whole assignment.
const LOCATION_TICK = 3000, LOCATION_FULL = 15000;

const previewAreas = q => q.location_check.status === 'confirmed' ? q.content.regions
  : q.location_check.result?.regions?.length ? q.location_check.result.regions : q.content.regions;
const drawable = area => [area.region?.x, area.region?.y, area.region?.width, area.region?.height].every(n => Number.isFinite(n) && n >= 0 && n <= 1);
const issuesOf = check => [...new Set([...(check.result?.issues || check.context.issues), ...(check.error ? [check.error] : [])])];

// Confirm all (requirement, 2026-09-25: flip through the answer areas, fix any with a problem in the review, then one click
// for the rest). It covers every question not yet confirmed whose printed points MATCH and whose preview can be drawn; the server
// confirms each exactly as its check suggested it. A check needing manual review, or areas with privacy masks, stays with the
// review dialog.
function confirmable(q) {
  const check = q.location_check, context = check?.context, areas = check ? previewAreas(q) : [];
  return check?.status === "needs_confirmation" && Boolean(context.template) && Boolean(context.printed_number) && areas.length > 0
    && areas.every(area => drawable(area) && area.page >= 1 && area.page <= context.template.page_count)
    && !q.content.regions.some(area => area.privacy_masks?.length);
}

// Retry (requirement, 2026-09-25): on a question showing no answer
// area, one click retries every such question whose check failed and has a retry left. A question with a suggested area is
// retried on its own. The server revalidates each check and retries all or none. Only failed checks are retried: a retry re-reads
// just the pages that failed, so a check that needs review would come back the same.
const retryable = check => Boolean(check) && check.status === "failed" && check.attempt < 2;
const retryableTogether = q => retryable(q.location_check) && !previewAreas(q).length;

// What Confirm all covers. In the step header only the new fact is shown (what is left out, what is still being read); the full
// explanation is the button's title (review fix GS-D4). A card without a header slot keeps the whole sentence.
function allScope(plan) {
  const open = inPrintedOrder(plan.questions).filter(q => q.location_check && q.location_check.status !== "confirmed");
  const together = open.filter(confirmable), reading = open.filter(q => ["pending", "running"].includes(q.location_check.status));
  const review = open.filter(q => !together.includes(q) && !reading.includes(q));
  return {together, reading, review};
}
function allTitle(plan) {
  const {together} = allScope(plan);
  return `Confirms the ${together.length === 1 ? "1 suggested answer area" : `${together.length} suggested answer areas`} not yet confirmed whose printed points match, as their previews show them.`;
}
function allMarkup(plan, h, {brief = false} = {}) {
  const {together, reading, review} = allScope(plan);
  if (!together.length) return "";
  const left = `${review.length ? ` Not included: ${h.e(review.map(versionTitle).join(", "))}.${brief ? "" : ` Confirm ${review.length === 1 ? "it" : "each"} with Review answer area.`}` : ""}${reading.length ? ` ${reading.length} still being read.` : ""}`.trim();
  if (brief) return left ? `<p class="supporting-copy" data-location-all-summary>${left}</p>` : "";
  return `<p class="supporting-copy" data-location-all-summary>${h.e(allTitle(plan))}${left ? ` ${left}` : ""}</p>`;
}

function areaPreview(q, h) {
  const check = q.location_check, template = check.context.template;
  const areas = previewAreas(q);
  if (!template || !areas.length) return '<div class="empty-state grading-area-empty"><h3>Answer area not ready yet</h3><p>The suggested area will appear here when the template check finishes.</p></div>';
  return `<div class="grading-area-previews">${[...new Set(areas.map(a => a.page))].map(page => `<figure><figcaption>${check.status === 'confirmed' ? 'Confirmed' : 'Suggested'} answer area · page ${h.e(page)}</figcaption><div class="grading-image"><img loading="lazy" src="/api/assignment-templates/${encodeURIComponent(template.id)}/pages/${page}/preview.webp" alt="Blank assignment page ${h.e(page)} with answer areas highlighted">${areas.map((area, index) => {
    if (area.page !== page) return '';
    if (!drawable(area)) return '';
    return `<div class="grading-highlight" data-preview-area="${index}"><span>Area ${index + 1}</span></div>`;
  }).join('')}</div></figure>`).join('')}</div>`;
}

// `onConfirmed(questions, plan)` runs after a confirmation is saved, before the page is drawn again.
// Each poll draws only what changed (FRONTEND "Compare with what was written"): a card, the header's Confirm button and its
// summary keep their nodes, so a preview image is not fetched again and focus stays on the button the instructor is on.
// Handlers are assigned as properties on every draw, so an unchanged button always acts on the latest reading.
const written = new WeakMap();
function write(node, markup) {
  if (written.get(node) === markup) return false;
  node.innerHTML = markup;
  written.set(node, markup);
  return true;
}
export function mountLocationChecks(host, plan, h, refresh, active, onUpdate = null, onConfirmed = null) {
  let latest = plan;
  function draw() {
    const together = latest.questions.filter(confirmable), summary = allMarkup(latest, h, {brief: Boolean(host.querySelector("[data-location-all-summary-slot]"))});
    const retryAll = inPrintedOrder(latest.questions).filter(retryableTogether);
    // Confirm all and what it covers appear ONCE, in the step header (2026-09-26 overhaul), never in every question's card. Its label
    // says what it does: confirm that many answer areas (grading-setup-14).
    const allSlot = host.querySelector("[data-location-all-slot]"), summarySlot = host.querySelector("[data-location-all-summary-slot]");
    if (allSlot) {
      write(allSlot, together.length ? `<button type="button" class="button primary" data-location-confirm-all title="${h.e(allTitle(latest))}">Confirm ${together.length} answer area${together.length === 1 ? "" : "s"}</button>` : "");
      const confirmAll = allSlot.querySelector("[data-location-confirm-all]");
      if (confirmAll) confirmAll.onclick = event => h.run(event.currentTarget, async () => {
        // The exact checks and question versions drawn: anything that changed since refuses the whole request.
        await h.api(`/api/grading/assignments/${plan.id}/locations/confirm-all`, "POST", {confirm_locations: true,
          questions: together.map(item => ({question_id: item.question_id, version: item.version, check_id: item.location_check.id, input_sha256: item.location_check.input_sha256}))});
        if (!active()) return;
        onConfirmed?.(together, latest);
        await refresh();
      });
    }
    if (summarySlot) write(summarySlot, summary);
    for (const q of latest.questions) {
      const panel = [...host.querySelectorAll("[data-rubric-question]")].find(node => node.dataset.rubricQuestion === q.question_id);
      if (!panel || !q.location_check) continue;
      let card = panel.querySelector("[data-location-check]");
      if (!card) { card = document.createElement("section"); card.dataset.locationCheck = ""; card.className = "grading-location-card"; (panel.querySelector("[data-location-slot]") || panel.querySelector("header")).after(card); }
      const check = q.location_check, context = check.context;
      const ready = ["needs_confirmation", "needs_review", "failed"].includes(check.status);
      const templates = latest.templates.filter(t => context.document_stamps_only || t.page_count === context.document_page_count);
      const retryMany = retryAll.includes(q) && retryAll.length > 1;
      const retryButton = !retryable(check) ? ''
        : retryMany ? `<button type="button" class="button" data-location-retry title="Retries every question without an answer area: ${h.e(retryAll.map(versionTitle).join(", "))}">Retry automatic checks (${retryAll.length})</button>`
        : '<button type="button" class="button" data-location-retry>Retry automatic check</button>';
      const confirmed = check.status === "confirmed";
      // A confirmed area keeps no reading of its own: say how its printed points were settled instead of "Not available".
      const printed = confirmed ? `<div><dt>Printed points</dt><dd>${/reviewed manually/i.test(check.label) ? "Reviewed manually" : "Matched when confirmed"}</dd></div>`
        : `<div><dt>Printed total read</dt><dd>${check.result?.printed_points != null ? `${h.e(check.result.printed_points)} points` : "Not available"}</dd></div>`;
      write(card, `<div class="grading-area-layout">${areaPreview(q, h)}<div class="grading-area-decision"><h4>${confirmed ? "Answer area confirmed" : "Check this answer area"}</h4>${confirmed ? "" : `<p role="status">${h.e(check.label)}</p>`}<dl class="grading-area-facts"><div><dt>Question</dt><dd>${h.e(context.printed_number || "Not identified")}</dd></div><div><dt>Rubric total</dt><dd>${h.e(context.rubric_maximum)} points</dd></div>${printed}</dl>${confirmed ? "" : '<p class="supporting-copy">Include every part of the question and all space where an answer may be written.</p>'}<div class="grading-actions">${ready ? `<button type="button" class="button${together.length && allSlot ? "" : " primary"}" data-location-open>Review answer area</button>` : ""}${retryButton}${["waiting_template", "waiting_templates"].includes(check.status) ? '<button type="button" class="button" data-location-template>Choose template…</button>' : templates.length > 1 ? '<button type="button" class="button" data-location-template>Change template…</button>' : ""}</div>${allSlot ? "" : summary}</div></div>`);
      // The overlays' coordinates live in their styles (CSP), not in the compared markup: they follow every reading, so a new
      // suggested area with the same status and page moves its highlight before Confirm sends that check.
      card.querySelectorAll('[data-preview-area]').forEach(overlay => {
        const r = previewAreas(q)[Number(overlay.dataset.previewArea)].region;
        Object.assign(overlay.style, {left: `${r.x * 100}%`, top: `${r.y * 100}%`, width: `${r.width * 100}%`, height: `${r.height * 100}%`});
      });
      const templateButton = card.querySelector("[data-location-template]"), openButton = card.querySelector("[data-location-open]"), retry = card.querySelector("[data-location-retry]");
      if (templateButton) templateButton.onclick = () => { location.hash = `#grading/${plan.id}?tab=rubrics&focus=templates`; };
      if (openButton) openButton.onclick = () => reviewLocation(latest, q, h, refresh, onConfirmed);
      if (retry) retry.onclick = event => h.run(event.currentTarget, async () => {
        if (retryMany) {
          await h.api(`/api/grading/assignments/${plan.id}/locations/retry-all`, "POST", {
            questions: retryAll.map(item => ({question_id: item.question_id, check_id: item.location_check.id, input_sha256: item.location_check.input_sha256}))});
        } else {
          await h.api(`/api/grading/assignments/${plan.id}/questions/${q.question_id}/locations/retry`, "POST", {check_id: check.id, input_sha256: check.input_sha256});
        }
        if (active()) await refresh();
      });
    }
  }
  draw();
  // While checks are read, the page looks at the local worker's counts every 3 s (a cheap read) and reads the whole assignment,
  // which it draws, when those counts changed, when it has none to compare with, or at least every 15 s: a check can start or
  // change its label without a page read. The counts are read first, so a change after them shows in the next look. While only a
  // template is awaited there is no local work, and the whole assignment is read every 15 s. A whole read that failed is tried
  // again at the next 3 s look. A hidden tab reads nothing, and reads the whole assignment once it is shown again.
  const reading = () => latest.questions.some(q => ["pending", "running"].includes(q.location_check?.status));
  const waiting = () => latest.questions.some(q => ["pending", "running", "waiting_template"].includes(q.location_check?.status));
  let work = null, fullAt = Date.now(), failed = false;
  async function readAll() {
    fullAt = Date.now(); failed = false;
    try {
      const updated = await h.api(`/api/grading/assignments/${plan.id}`);
      if (!active() || !host.isConnected) return;
      latest = updated; draw(); onUpdate?.(updated);
    } catch (error) {
      work = null; failed = true;
      const card = host.querySelector("[data-location-check]"), status = card?.querySelector("[role=status]");
      if (active() && status) {
        status.replaceChildren(document.createTextNode(`Verification status could not refresh: ${error.message}`));
        written.delete(card);  // The next good read writes the card again, whatever it holds.
      }
    }
  }
  async function poll(shown = false) {
    if (!active() || !host.isConnected) return;
    if (document.hidden) { whenVisible(() => { void poll(true); }); return; }
    let full = shown || work === null || Date.now() - fullAt >= LOCATION_FULL;
    if (reading()) {
      try {
        const text = JSON.stringify(await h.api(`/api/grading/assignments/${plan.id}/local-work`));
        if (!active() || !host.isConnected) return;
        full ||= text !== work;
        work = text;
      } catch { full = true; work = null; }
    } else work = null;
    if (full) await readAll();
    if (active() && host.isConnected && waiting()) setTimeout(() => { void poll(); }, reading() || failed ? LOCATION_TICK : LOCATION_FULL);
  }
  if (waiting()) setTimeout(() => { void poll(); }, reading() ? LOCATION_TICK : LOCATION_FULL);
}

// The answer area's review window. The required tick and any refusal sit in the footer beside Confirm (grading-setup-4): at the
// foot of the template pages they were ~2,000 px down, and Confirm seemed to do nothing. With no suggested area the pages open
// and a rectangle dragged on one becomes the first area (missed:grading-setup[0]).
// Its accessible name starts with the words shown beside it (WCAG 2.5.3).
const CONFIRM_TICK = "I checked every part and continuation page: I inspected and confirm every answer area, including all parts and continuation pages";
function reviewLocation(plan, q, h, refresh, onConfirmed) {
  const check = q.location_check, context = check.context, result = check.result;
  const manual = check.status !== "needs_confirmation";
  const areas = result?.regions?.length ? result.regions : q.content.regions;
  const issues = issuesOf(check);
  const hasMasks = q.content.regions.some(area => area.privacy_masks?.length);
  const hint = count => count ? "Inspect the number and adjacent point total on the page. Drag a rectangle on a page to replace the selected answer area, or edit its percentages below. Include every part and all the space where an answer may be written."
    : "No answer area was suggested: drag a rectangle on its page around where the answer is written. Include every part and any continuation page.";
  const form = h.dialog("Confirm the answer area", areas.length ? "Every suggested area needs your confirmation before it can be used for grading." : "Draw the answer area, then confirm it.", `<p><strong>Rubric maximum: ${h.e(context.rubric_maximum)} points.</strong> ${result?.printed_points != null ? `Printed reading: ${h.e(result.printed_points)} points.` : "No reliable printed total was read."}</p>${issues.length ? `<div class="grading-warning"><strong>Manual review required</strong><ul>${issues.map(issue => `<li>${h.e(issue)}</li>`).join("")}</ul></div>` : ""}${h.select("Template to confirm", "template_id", context.templates.map(t => [t.id, t.label]), context.template?.id || "")}${h.field("Printed question number", "printed_number", context.printed_number, "text", 'required inputmode="numeric" maxlength="4"')}<p class="supporting-copy" data-location-hint>${h.e(hint(areas.length))}</p><div data-location-pages></div><details><summary>Answer areas and privacy masks (advanced)</summary>${h.area("Answer areas (JSON)", "regions", JSON.stringify(areas, null, 2))}</details><label class="grading-check quality-confirm-tick"><input type="checkbox" name="confirm_locations" aria-label="${h.e(CONFIRM_TICK)}"><span>I checked every part and continuation page</span></label><div data-location-manual ${manual ? "" : "hidden"}>${h.check("I manually reviewed and resolved the failed question and point checks", "manual_review")}</div>${h.area(manual ? "Required explanation of the manual resolution" : "Review notes", "notes", "", manual ? "required" : "")}${hasMasks ? h.check("I reviewed privacy masks for the changed answer areas", "review_masks") : ""}`, "Confirm answer area", async (values, _, live) => {
    if (!values.has("confirm_locations")) throw new Error("Look at every part and continuation page, then tick “I checked every part and continuation page”.");
    if ((manual || values.get("printed_number") !== context.printed_number) && !values.has("manual_review")) throw new Error("Confirm your manual review of the question and point checks.");
    await h.api(`/api/grading/assignments/${plan.id}/questions/${q.question_id}/locations/confirm`, "POST", {
      version: q.version, check_id: check.id, input_sha256: check.input_sha256,
      template_id: values.get("template_id"), printed_number: values.get("printed_number"), regions: JSON.parse(values.get("regions")),
      confirm_locations: values.has("confirm_locations"), manual_review: values.has("manual_review"), notes: values.get("notes"), review_masks: values.has("review_masks")});
    if (live()) { h.close(); onConfirmed?.([q], plan); await refresh(); }
  });
  // The tick and the refusal go beside the buttons, as in the rubric finalization's window (grading.css .quality-confirm-tick).
  form.querySelector(".dialog-footer").prepend(form.querySelector(".form-error"), form.querySelector('[name="confirm_locations"]').closest("label"));
  const dialog = document.querySelector("#dialog");
  dialog.classList.add("dialog-wide");
  dialog.addEventListener("close", () => dialog.classList.remove("dialog-wide"), {once: true});
  form.elements.printed_number.addEventListener("input", () => {
    const needed = manual || form.elements.printed_number.value !== context.printed_number;
    form.querySelector("[data-location-manual]").hidden = !needed;
    form.elements.notes.required = needed;
  });
  mountRegions(form, h);
  const pageHost = form.querySelector("[data-location-pages]");
  // The area a drag replaces; with none yet, "New area": the drag adds it, and it is the one selected afterwards.
  let choose = null;
  function preview() {
    const template = context.templates.find(t => t.id === form.elements.template_id.value);
    let regions;
    try { regions = JSON.parse(form.elements.regions.value); } catch { return; }
    if (!template || !Array.isArray(regions)) return;
    const selectedArea = choose ?? (Number(form.elements.drawing_area?.value) < regions.length ? form.elements.drawing_area.value : "0");
    choose = null;
    form.querySelector("[data-location-hint]").textContent = hint(regions.length);
    const options = regions.length ? regions.map((_, index) => [index, `Area ${index+1}`]) : [[0, "New area"]];
    pageHost.innerHTML = `${h.select("Area to draw", "drawing_area", options, selectedArea)}${Array.from({length: template.page_count}, (_, index) => {
      const page = index+1;
      return `<details class="grading-location-page" ${!regions.length || regions.some(r => r.page === page) ? "open" : ""}><summary>Template page ${page}</summary><div class="grading-image" data-location-page="${page}"><img loading="lazy" src="/api/assignment-templates/${encodeURIComponent(template.id)}/pages/${page}.png" alt="Blank template page ${page}"><div data-location-overlays></div></div></details>`;
    }).join("")}`;
    overlays();
    pageHost.querySelectorAll("[data-location-page]").forEach(node => {
      let start = null;
      const point = event => { const rect = node.getBoundingClientRect(); return [Math.max(0, Math.min(1, (event.clientX-rect.left)/rect.width)), Math.max(0, Math.min(1, (event.clientY-rect.top)/rect.height))]; };
      node.onpointerdown = event => { if (event.button !== 0) return; start = point(event); node.setPointerCapture(event.pointerId); event.preventDefault(); };
      node.onpointercancel = () => { start = null; };
      node.onpointerup = event => {
        if (!start) return;
        const end = point(event), first = start; start = null;
        const width = Math.abs(first[0]-end[0]), height = Math.abs(first[1]-end[1]);
        if (width < .005 || height < .005) return;
        let current;
        try { current = JSON.parse(form.elements.regions.value); } catch { return; }
        if (!Array.isArray(current)) return;
        const index = Number(form.elements.drawing_area.value);
        const region = {x: Math.min(first[0],end[0]), y: Math.min(first[1],end[1]), width, height}, page = Number(node.dataset.locationPage);
        if (current[index]) current[index] = {...current[index], page, region};
        else if (index === current.length) { current.push({page, region, privacy_masks: []}); choose = String(index); }
        else return;
        form.elements.regions.value = JSON.stringify(current, null, 2);
        form.elements.regions.dispatchEvent(new Event("change"));
      };
    });
  }
  function overlays() {
    let regions;
    try { regions = JSON.parse(form.elements.regions.value); } catch { return; }
    if (!Array.isArray(regions)) return;
    pageHost.querySelectorAll("[data-location-page]").forEach(node => {
      const host = node.querySelector("[data-location-overlays]");
      host.replaceChildren();
      regions.forEach((area, i) => {
        if (area.page !== Number(node.dataset.locationPage) || !area.region) return;
        const r = area.region, coords = [r.x, r.y, r.width, r.height];
        if (!coords.every(v => Number.isFinite(v) && v >= 0 && v <= 1)) return;
        const overlay = document.createElement("div"), label = document.createElement("span");
        overlay.className = "grading-highlight"; label.textContent = `Area ${i+1}`;
        Object.assign(overlay.style, {left: `${r.x*100}%`, top: `${r.y*100}%`, width: `${r.width*100}%`, height: `${r.height*100}%`});
        overlay.append(label); host.append(overlay);
      });
    });
  }
  form.elements.template_id.addEventListener("change", preview);
  form.elements.regions.addEventListener("change", preview);
  form.addEventListener("input", overlays);
  preview();
}

// ALL ANSWER AREAS ("Whole exam" in the plan; 2026-09-26 overhaul): every question's answer area outlined on its version's blank pages, so a missed or overlapping
// area shows at a glance. A view of the saved areas (confirmed) and the checks' suggestions; it stores nothing, and choosing an area
// only opens its question below. Images load when the disclosure is first opened.
function wholeGroups(plan) {
  const groups = new Map();
  for (const q of inPrintedOrder(plan.questions)) {
    const template = q.location_check?.context?.template;
    if (!template || !q.location_check) continue;
    if (!groups.has(template.id)) groups.set(template.id, {key: template.id, template, label: q.exam_version ? `Version ${q.exam_version}` : template.label, questions: []});
    groups.get(template.id).questions.push(q);
  }
  return [...groups.values()];
}

export function wholeExamMarkup(plan) {
  if (!wholeGroups(plan).length) return "";
  return `<details class="disclosure grading-whole-exam" data-whole-exam><summary><span>All answer areas</span><span class="disclosure-hint">outlined on the blank pages</span></summary><div class="disclosure-body" data-whole-exam-body></div></details>`;
}

const wholeState = new WeakMap();
export function mountWholeExam(host, plan, h, onSelect) {
  const node = host.querySelector("[data-whole-exam]");
  if (!node) return;
  const known = wholeState.get(node);
  const state = known || {version: "", drawn: ""};
  Object.assign(state, {plan, onSelect});
  if (!known) {
    wholeState.set(node, state);
    node.addEventListener("toggle", () => { if (node.open) draw(); });
    const body = node.querySelector("[data-whole-exam-body]");
    bindFilterChips(body, "whole-exam", value => { state.version = value; state.drawn = ""; draw(); });
    body.addEventListener("click", event => {
      const area = event.target.closest?.("[data-whole-question]");
      if (area) state.onSelect?.(area.dataset.wholeQuestion);
    });
  }
  if (node.open) draw();
  function draw() {
    const body = node.querySelector("[data-whole-exam-body]"), groups = wholeGroups(state.plan);
    const group = groups.find(g => g.key === state.version) || groups[0];
    if (!group) { body.replaceChildren(); return; }
    state.version = group.key;
    const areas = group.questions.flatMap(q => previewAreas(q).map((area, index) => ({q, area, index}))).filter(({area}) => drawable(area));
    // Redraw only when what is shown changed: a background poll must not reload the pages under the instructor.
    const signature = JSON.stringify([group.key, groups.map(g => g.key), areas.map(({q, area}) => [q.question_id, q.location_check.status, area.page, area.region])]);
    if (signature === state.drawn) return;
    state.drawn = signature;
    const pages = Array.from({length: group.template.page_count || 0}, (_, i) => i + 1);
    body.innerHTML = `${groups.length > 1 ? filterChipsMarkup("whole-exam", groups.map(g => ({value: g.key, label: g.label, count: g.questions.length})), group.key, {label: "Version to show"}) : ""}
      <p class="supporting-copy">${h.e(group.template.label)} · solid outlines are confirmed, dashed ones suggested. Choose an area to open its question.</p>
      <div class="grading-whole-pages">${pages.map(page => `<figure class="grading-whole-page"><figcaption>Page ${h.e(page)}</figcaption><div class="grading-image"><img loading="lazy" src="/api/assignment-templates/${encodeURIComponent(group.template.id)}/pages/${page}/preview.webp" alt="Blank page ${h.e(page)} of ${h.e(group.label)}">${areas.filter(({area}) => area.page === page).map(({q, index}) => {
        const confirmed = q.location_check.status === "confirmed", name = q.location_check.context.printed_number || q.content.title;
        return `<button type="button" class="grading-whole-area" data-state="${confirmed ? "confirmed" : "suggested"}" data-whole-question="${h.e(q.question_id)}" data-whole-area="${index}" aria-label="${h.e(`${versionTitle(q)}: answer area ${index + 1}, ${confirmed ? "confirmed" : "suggested"}`)}"><span>${h.e(name)}</span></button>`;
      }).join("")}</div></figure>`).join("")}</div>`;
    for (const button of body.querySelectorAll("[data-whole-question]")) {
      const q = group.questions.find(item => item.question_id === button.dataset.wholeQuestion), r = previewAreas(q)[Number(button.dataset.wholeArea)].region;
      Object.assign(button.style, {left: `${r.x * 100}%`, top: `${r.y * 100}%`, width: `${r.width * 100}%`, height: `${r.height * 100}%`});
    }
  }
}
