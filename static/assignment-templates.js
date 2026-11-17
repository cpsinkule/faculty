// Reusable course-owned grading templates. Saving a map and applying it to a
// term's printed version are separate, explicit actions; no reading or scoring.
import { runFormAction, dialogSession, registerLeaveGuard } from "./ui.js";
import { viewLink } from "./file-viewer.js";

const remembered = new Map();
const fullPage = () => ({x: 0, y: 0, width: 1, height: 1});
const regions = question => question.regions?.length ? question.regions : [{page: question.page || 1, region: question.region || fullPage()}];
const normalized = value => Math.round(Math.max(0, Math.min(1, value)) * 10000) / 10000;

export function createAssignmentTemplates({api, escape, toast, showDialog, dialogHeader}) {
  const e = value => escape(String(value ?? ""));
  const base = "/api/assignment-templates";
  let state = null, generation = 0;
  const manager = () => document.getElementById("assignment-template-manager");
  const detailPath = id => `${base}/${encodeURIComponent(id)}`;
  const LEAVE_QUESTION = "Discard the unsaved problem mapping?";
  const confirmLeave = () => !state?.dirty || window.confirm(LEAVE_QUESTION);
  const safeUrl = value => typeof value === "string" && value.startsWith("/api/assignment-templates/") ? value : "";
  function perform(form, operation) {
    return runFormAction(form, async () => {
      const active = state; active.busy = true;
      const controls = [...manager().querySelectorAll('input, select, textarea, button:not([data-template-close]):not([data-action="close"])')];
      const prior = controls.map(control => control.disabled);
      controls.forEach(control => { control.disabled = true; });
      try { return await operation(); }
      finally { active.busy = false; controls.forEach((control, index) => { control.disabled = prior[index]; }); }
    });
  }
  function dirty() {
    state.dirty = true;
    manager().querySelector("[data-template-status]").textContent = "Unsaved mapping";
    const apply = manager().querySelector("[data-template-apply]");
    if (apply) apply.disabled = true;
  }
  function close() {
    if (!confirmLeave()) return;
    state.dirty = false;
    document.getElementById("dialog").close();
  }
  let cleanup = () => {};
  async function open(context) {
    cleanup();
    const token = ++generation;
    state = {context, list: [], detail: null, draft: [], question: 0, area: 0, dirty: false, uploadId: crypto.randomUUID()};
    showDialog(`<div id="assignment-template-manager">${dialogHeader("Grading templates", "Choose the paper, then show where each problem belongs.")}<div class="dialog-body"><p class="section-lead">Upload a blank exam or assignment and map its answer pages.</p><p class="supporting-copy">Keep a reusable reference for this assignment, with a saved map of each problem’s answer areas.</p><div data-template-library></div><details class="template-upload" open><summary>Upload a template</summary><form id="assignment-template-upload"><label class="field">Template label<input name="label" required maxlength="160" placeholder="Main exam · Version A"></label><label class="field">Template files<input name="files" type="file" multiple required accept=".pdf,.docx,.tex,.latex,.png,.jpg,.jpeg"></label><p class="field-hint">For an exam already printed, use its original exported PDF to preserve the page layout. Word files may paginate differently. You can also choose one DOCX or LaTeX source with its supporting files; originals stay with the template.</p><details><summary>LaTeX entry point, optional</summary><label class="field">Entry point<input name="entrypoint" placeholder="main.tex"></label><p class="field-hint">Name the main file if you upload several LaTeX files.</p></details><p class="form-error" role="alert" hidden></p><button class="button primary" type="submit">Upload template</button><span class="field-hint" data-upload-status role="status"></span></form></details><div data-template-content><p role="status">Loading templates…</p></div></div><footer class="dialog-footer"><span class="field-hint" data-template-status role="status"></span><button class="button" type="button" data-template-close>Close</button></footer></div>`);
    const host = manager(), dialog = document.getElementById("dialog"), current = dialogSession(host, dialog);
    dialog.classList.add("dialog-wide", "template-dialog");
    const cancel = event => { event.preventDefault(); close(); };
    const clicks = event => { if (event.target.closest('[data-action="close"], [data-template-close]')) { event.preventDefault(); event.stopPropagation(); close(); } };
    // An unsaved mapping: the shell asks before a hash change (Back) or closing the tab, with the dialog's own question.
    const unguard = registerLeaveGuard(() => state?.dirty && current() ? LEAVE_QUESTION : null);
    dialog.addEventListener("cancel", cancel); host.addEventListener("click", clicks, true);
    const dispose = () => { dialog.removeEventListener("close", dispose); dialog.removeEventListener("cancel", cancel); host.removeEventListener("click", clicks, true); unguard(); dialog.classList.remove("template-dialog", "dialog-wide"); };
    cleanup = dispose; dialog.addEventListener("close", dispose, {once: true});
    const upload = host.querySelector("#assignment-template-upload");
    upload.addEventListener("input", () => { state.uploadId = crypto.randomUUID(); });
    upload.onsubmit = event => {
      event.preventDefault();
      if (!confirmLeave()) return;
      const request = ++generation;
      const body = new FormData(upload); body.set("assignment_id", context.assignmentId); body.set("request_id", state.uploadId);
      void perform(upload, async () => {
        host.querySelector("[data-upload-status]").textContent = "Preparing the page previews…";
        try {
          const result = await api(base, "POST", body, {}, 240000);
          if (!current() || request !== generation) return;
          try {
            const library = await api(`${base}?assignment_id=${encodeURIComponent(context.assignmentId)}`);
            if (!current() || request !== generation) return;
            state.list = library.templates.filter(item => item.id !== result.id);
          } catch (cause) { if (current()) toast(`Template uploaded; the saved-template list could not refresh. ${cause.message}`, true); }
          if (!current() || request !== generation) return;
          state.list = state.list.filter(item => item.id !== result.id); state.list.push(result); state.uploadId = crypto.randomUUID(); upload.reset();
          host.querySelector(".template-upload").open = false; useDetail(result); toast("Template uploaded. Map its problems when ready.");
        } finally { if (current()) host.querySelector("[data-upload-status]").textContent = ""; }
      });
    };
    try {
      const result = await api(`${base}?assignment_id=${encodeURIComponent(context.assignmentId)}`);
      if (!current() || token !== generation) return;
      state.list = result.templates; renderLibrary();
      if (state.list.length) { host.querySelector(".template-upload").open = false; await load(state.list.find(item => item.id === (context.templateId || remembered.get(context.assignmentId)))?.id || state.list[0].id); }
      else host.querySelector("[data-template-content]").innerHTML = '<p class="template-empty">Upload the blank paper to begin. You can choose whole pages or mark smaller answer areas.</p>';
    } catch (cause) { if (current() && token === generation) host.querySelector("[data-template-content]").textContent = cause.message; }
  }
  function renderLibrary() {
    const host = manager();
    host.querySelector("[data-template-library]").innerHTML = state.list.length ? `<label class="field">Saved template<select aria-label="Saved template">${state.list.map(item => `<option value="${e(item.id)}" ${item.id === state.detail?.id ? "selected" : ""}>${e(item.label)} · ${item.page_count} page${item.page_count === 1 ? "" : "s"}</option>`).join("")}</select></label>` : "";
    const select = host.querySelector('[aria-label="Saved template"]');
    if (select) select.onchange = () => { if (!confirmLeave()) { select.value = state.detail.id; return; } void load(select.value); };
  }
  async function load(id) {
    const token = ++generation, host = manager(), current = dialogSession(host, document.getElementById("dialog"));
    const select = host.querySelector('[aria-label="Saved template"]');
    const active = state; active.busy = true;
    const controls = [...host.querySelectorAll('input, select, textarea, button:not([data-template-close]):not([data-action="close"])')];
    const prior = controls.map(control => control.disabled);
    controls.forEach(control => { control.disabled = true; });
    try {
      const result = await api(detailPath(id));
      if (current() && token === generation) useDetail(result);
    } catch (cause) { if (current() && token === generation) { select.value = state.detail?.id || ""; toast(cause.message, true); } }
    finally { active.busy = false; controls.forEach((control, index) => { control.disabled = prior[index]; }); }
  }
  function useDetail(detail) {
    state.detail = detail; remembered.set(state.context.assignmentId, detail.id);
    state.draft = structuredClone(detail.questions || []).map(question => ({...question, regions: regions(question)}));
    state.question = 0; state.area = 0; state.dirty = false;
    renderLibrary(); renderMapping();
  }
  function renderMapping() {
    const host = manager(), detail = state.detail, question = state.draft[state.question];
    const links = [[detail.pdf_url, "View template PDF"], [detail.source_url, "Download original files"], [detail.package_url, "Download OCR/VLM package"]];
    host.querySelector("[data-template-content]").innerHTML = `<section class="template-summary"><div><h3>${e(detail.label)}</h3><p>${detail.page_count} page${detail.page_count === 1 ? "" : "s"} · ${e(detail.source_format)} · ${state.draft.length} problem${state.draft.length === 1 ? "" : "s"}</p></div><div class="template-links">${links.filter(([url]) => safeUrl(url)).map(([url, label]) => url === detail.pdf_url ? `<a ${viewLink(url, detail.label || "Template", e)}>${label}</a>` : `<a href="${e(url)}" download>${label}</a>`).join("")}</div></section><form id="assignment-template-mapping"><div class="template-mapping-toolbar"><label class="field">Problem<select aria-label="Problem" ${state.draft.length ? "" : "disabled"}>${state.draft.map((item, index) => `<option value="${index}" ${index === state.question ? "selected" : ""}>${e(item.label)} · ${item.max_points} points</option>`).join("")}</select></label><button class="button" type="button" data-problem-add>Add problem</button><button class="text-button" type="button" data-mapping-reload>Reload saved mapping</button></div>${question ? `<div class="template-editor-layout"><section class="template-problem"><div class="template-field-pair"><label class="field">Problem label<input name="label" required maxlength="200" value="${e(question.label)}"></label><label class="field">Maximum points<input name="max_points" type="number" min="0.01" step="any" required value="${e(question.max_points)}"></label></div><details><summary>Problem ID</summary><label class="field">Stable problem ID<input name="id" required pattern="[A-Za-z0-9](?:[A-Za-z0-9_]|-)*" maxlength="60" value="${e(question.id)}"></label></details><fieldset class="template-pages"><legend>Answer pages</legend><p class="field-hint">Choose every page used by this problem. Each page starts with the full page selected.</p>${detail.pages.map(page => `<label class="checkbox-field"><input type="checkbox" data-template-page="${page.number}" ${question.regions.some(area => area.page === page.number) ? "checked" : ""}>Page ${page.number}</label>`).join("")}</fieldset><label class="field">Answer area<select aria-label="Answer area">${question.regions.map((area, index) => `<option value="${index}" ${index === state.area ? "selected" : ""}>Page ${area.page} · area ${index + 1}</option>`).join("")}</select></label><div class="template-area-actions"><button class="button" type="button" data-area-add ${question.regions.length ? "" : "disabled"}>Add area on this page</button><button class="text-button" type="button" data-area-full ${question.regions.length ? "" : "disabled"}>Use full page</button><button class="text-button" type="button" data-area-remove ${question.regions.length ? "" : "disabled"}>Remove area</button></div><details class="template-coordinates"><summary>Area coordinates</summary><p class="field-hint">Fractions of the page: 0 is the left or top edge and 1 is the far edge. You can enter an area here instead of drawing.</p><div class="template-field-pair">${[["x", "Left"], ["y", "Top"], ["width", "Width"], ["height", "Height"]].map(([key, label]) => `<label class="field">${label}<input type="number" data-area-coordinate="${key}" min="0" max="1" step="any" value="${e(question.regions[state.area]?.region[key] ?? fullPage()[key])}" ${question.regions.length ? "" : "disabled"}></label>`).join("")}</div></details><button class="text-button danger-button" type="button" data-problem-remove>Remove problem</button></section><section class="template-preview"><h4 data-preview-heading></h4><p class="field-hint">Drag across the page to replace the selected answer area. Use Area coordinates for keyboard entry.</p><div class="template-page-frame" data-template-frame><img alt="" draggable="false"><div class="template-area-overlay" aria-hidden="true"></div></div><p class="field-hint" data-preview-status role="status"></p></section></div>` : '<div class="template-empty"><h3>Where does each problem belong?</h3><p>Add a problem, choose its pages, and optionally draw a smaller answer area.</p></div>'}<p class="form-error" role="alert" hidden></p><div class="template-save-row"><button class="button primary" type="submit" ${state.draft.length ? "" : "disabled"}>Save mapping</button><p class="field-hint">Saving retains a new mapping revision. Existing exam rubrics stay unchanged.</p></div></form>${state.context.eventId ? `<section class="template-apply"><h3>Use this mapping for grading</h3><p>Apply the saved mapping to ${e(state.context.assignmentVersionLabel || "the selected printed version")}. This changes this exam's rubric; existing grades may need review.</p><p class="field-hint">Compare the preview with the actual printed paper: matching page counts alone does not establish matching layouts. Save any mapping changes first.</p>${state.context.assignmentVersionId && detail.page_count !== state.context.pageCount ? `<p class="notice">This template has ${detail.page_count} page${detail.page_count === 1 ? "" : "s"}; the selected printed version has ${e(state.context.pageCount)}. Choose a template with the same page count before applying.</p>` : ""}<form id="assignment-template-apply"><p class="form-error" role="alert" hidden></p><button class="button" type="submit" data-template-apply ${!detail.revision_id || state.dirty || !state.context.assignmentVersionId || detail.page_count !== state.context.pageCount ? "disabled" : ""}>Apply saved mapping</button></form></section>` : ""}`;
    host.querySelector("[data-template-status]").textContent = state.dirty ? "Unsaved mapping" : detail.version ? `Saved mapping · revision ${detail.version}` : "No mapping saved yet";
    bindMapping();
  }
  function bindMapping() {
    const host = manager(), form = host.querySelector("#assignment-template-mapping"), question = state.draft[state.question];
    const change = callback => { callback(); dirty(); renderMapping(); };
    form.querySelector('[aria-label="Problem"]').onchange = event => { state.question = Number(event.target.value); state.area = 0; renderMapping(); };
    form.querySelector("[data-mapping-reload]").onclick = () => { if (confirmLeave()) void load(state.detail.id); };
    form.querySelector("[data-problem-add]").onclick = () => change(() => {
      let number = state.draft.length + 1; while (state.draft.some(item => item.id === `q${number}`)) number++;
      state.draft.push({id: `q${number}`, label: `Problem ${number}`, max_points: 10, criteria: [], feedback: [], regions: [{page: 1, region: fullPage()}]});
      state.question = state.draft.length - 1; state.area = 0;
    });
    if (question) {
      for (const name of ["id", "label", "max_points"]) form.elements[name].oninput = event => { question[name] = name === "max_points" ? Number(event.target.value) : event.target.value; dirty(); };
      form.querySelectorAll("[data-template-page]").forEach(input => { input.onchange = () => change(() => {
        const page = Number(input.dataset.templatePage);
        if (input.checked) { question.regions.push({page, region: fullPage()}); state.area = question.regions.length - 1; }
        else { question.regions = question.regions.filter(area => area.page !== page); state.area = 0; }
      }); });
      form.querySelector('[aria-label="Answer area"]').onchange = event => { state.area = Number(event.target.value); renderMapping(); };
      form.querySelector("[data-area-add]").onclick = () => change(() => { question.regions.push({page: question.regions[state.area].page, region: {x: .1, y: .1, width: .8, height: .3}}); state.area = question.regions.length - 1; });
      form.querySelector("[data-area-full]").onclick = () => change(() => { question.regions[state.area].region = fullPage(); });
      form.querySelector("[data-area-remove]").onclick = () => change(() => { question.regions.splice(state.area, 1); state.area = 0; });
      form.querySelector("[data-problem-remove]").onclick = () => change(() => { state.draft.splice(state.question, 1); state.question = Math.max(0, state.question - 1); state.area = 0; });
      form.querySelectorAll("[data-area-coordinate]").forEach(input => { input.oninput = () => { question.regions[state.area].region[input.dataset.areaCoordinate] = Number(input.value); dirty(); drawOverlay(); }; });
      bindPreview();
    }
    form.onsubmit = event => { event.preventDefault(); const current = dialogSession(form, document.getElementById("dialog"));
      void perform(form, async () => {
        if (state.draft.some(item => !item.regions.length)) throw new Error("Choose at least one answer page for every problem.");
        const detail = await api(`${detailPath(state.detail.id)}/mapping`, "PUT", {version: state.detail.version, questions: state.draft});
        if (current()) { useDetail(detail); toast("Problem mapping saved."); }
      });
    };
    const apply = host.querySelector("#assignment-template-apply");
    if (apply) apply.onsubmit = event => { event.preventDefault(); const current = dialogSession(apply, document.getElementById("dialog"));
      void perform(apply, async () => {
        if (state.dirty) throw new Error("Save the mapping before applying it.");
        const context = state.context;
        await api(`/api/exams/${encodeURIComponent(context.eventId)}/grading/template`, "POST", {assignment_version_id: context.assignmentVersionId, template_revision_id: state.detail.revision_id, version: context.gradingVersion});
        if (current()) { document.getElementById("dialog").close(); await context.onApplied?.(); toast("Saved mapping applied to this exam version."); }
      });
    };
  }
  function drawOverlay() {
    const area = state.draft[state.question]?.regions[state.area], overlay = manager().querySelector(".template-area-overlay");
    if (!overlay) return;
    overlay.hidden = !area;
    if (area) for (const [property, key] of [["left", "x"], ["top", "y"], ["width", "width"], ["height", "height"]]) overlay.style[property] = `${area.region[key] * 100}%`;
  }
  function bindPreview() {
    const host = manager(), frame = host.querySelector("[data-template-frame]"), area = state.draft[state.question].regions[state.area];
    const page = state.detail.pages.find(item => item.number === area?.page);
    frame.hidden = !page; if (!page) return;
    const image = frame.querySelector("img"), status = host.querySelector("[data-preview-status]");
    host.querySelector("[data-preview-heading]").textContent = `Page ${page.number}`;
    image.alt = `Blank template page ${page.number}`; image.src = safeUrl(page.image_url);
    image.onerror = () => { status.textContent = "The page preview could not be loaded. You can still enter coordinates or open the template PDF."; };
    image.onload = () => { status.textContent = ""; drawOverlay(); }; drawOverlay();
    let start = null;
    const point = event => { const box = image.getBoundingClientRect(); return {x: normalized((event.clientX - box.left) / box.width), y: normalized((event.clientY - box.top) / box.height)}; };
    frame.onpointerdown = event => { if (state.busy || event.button !== 0 || !image.naturalWidth) return; start = {point: point(event), previous: {...area.region}}; frame.setPointerCapture(event.pointerId); event.preventDefault(); };
    frame.onpointermove = event => { if (!start) return; const end = point(event); area.region = {x: Math.min(start.point.x, end.x), y: Math.min(start.point.y, end.y), width: Math.abs(end.x - start.point.x), height: Math.abs(end.y - start.point.y)}; drawOverlay(); };
    frame.onpointerup = event => { if (!start) return; if (area.region.width < .002 || area.region.height < .002) area.region = start.previous; else dirty(); start = null; frame.releasePointerCapture(event.pointerId); drawOverlay(); host.querySelectorAll("[data-area-coordinate]").forEach(input => { input.value = area.region[input.dataset.areaCoordinate]; }); };
    frame.onpointercancel = () => { if (start) { area.region = start.previous; start = null; drawOverlay(); } };
  }
  return {open};
}
