// A line added to every final grade of one question in one section, from Certification and Export (requirement, 2026-09-27). A question is
// one version's, so adding asks for the section (or every section), the line's wording and its points; editing asks for new wording
// or points and removing asks for nothing, both acting only on the grades that batch changed which still carry its line.
// Preview (POST …/questions/<qid>/adjustments/preview) writes nothing and lists each final grade before and after and every response
// left alone with its reason; the dialog's primary then says what applying does ("Add to N grades", "Change on N grades", "Remove from
// N grades"), which POSTs the same request with {fingerprint, request_id, confirmation: "adjust"}. The server plans again and refuses
// (409) anything that changed; each grade becomes one ordinary review of the instructor's, the rubric does not change and nothing is
// regraded. Editing the line after a preview asks for a new preview. A request whose answer never came is kept in memory and only an
// explicit Retry sends the SAME body (and request ID) again; the server answers a repeated request ID with what it recorded.
import {calloutMarkup, tableMarkup, disclosureMarkup, runFormAction, seenByMarkup} from "./ui.js";
import {signedPoints} from "./grading-certification-question.js";

export const REASON_LIMIT = 200;
export const EVERY = "all";
export const signed = signedPoints;
const n = value => Number(value || 0).toLocaleString();
const plural = (count, one, many = `${one}s`) => `${n(count)} ${Number(count) === 1 ? one : many}`;
const lineText = line => line ? `“${line.reason}” (${signed(line.points)})` : "";
const unresolved = new Map();  // `${plan}:${question}:${action}:${batch}` -> {body}
const TITLES = {add: "Add a line to every grade", edit: "Change a line on every grade", remove: "Remove a line from every grade"};

// ------------------------------------------------------------------------------------------------------------------------------
// Pure pieces (tests/test_grading_certification.mjs)

// What is wrong with the line as typed ("" when nothing), checked before anything is sent; the server checks it all again. `old`:
// the batch line being edited (an edit must change it).
export function lineProblem({section = "", points = "", reason = ""} = {}, max = null, old = null) {
  if (!section) return "Choose a section, or every section.";
  if (!String(reason).trim()) return "Write the line's wording.";
  if (String(reason).trim().length > REASON_LIMIT) return `Keep the line's wording to ${REASON_LIMIT} characters.`;
  if (String(points).trim() === "" || !Number.isFinite(Number(points))) return "Give the line's points.";
  const value = Number(points);
  if (value === 0) return "An adjustment of 0 points changes no grade.";
  if (Math.abs(value * 100 - Math.round(value * 100)) > 1e-6) return "Give the points with at most two decimals.";
  if (max !== null && max !== undefined && Number.isFinite(Number(max)) && Math.abs(value) > Number(max)) return `The question is worth ${max}; an adjustment can be at most that much.`;
  if (old && String(reason).trim() === String(old.reason) && value === Number(old.points)) return "Change the line's wording or its points.";
  return "";
}

// The body both requests send for an action (`batch`: the batch being edited or removed).
export function requestBody(action, {section, points, reason} = {}, batch = null) {
  if (action === "remove") return {action, batch_id: batch?.batch_id};
  const line = {points: Number(points), reason: String(reason || "").trim()};
  return action === "edit" ? {action, batch_id: batch?.batch_id, ...line} : {section_id: section, ...line};
}

// The preview's sentence: how many final grades change and how many responses are left alone.
export function summaryText(preview) {
  const c = preview?.counts || {}, action = preview?.action || "add", count = Number(c.change || 0);
  const where = preview?.section_id === EVERY ? "in every section" : `in section ${preview?.section}`;
  const left = Number(c.left || 0) ? ` ${plural(c.left, "response")} ${Number(c.left) === 1 ? "is" : "are"} left alone.` : "";
  if (action === "edit") return count ? `${plural(count, "grade")} ${count === 1 ? "has" : "have"} ${lineText(preview.old)} changed to ${lineText(preview)}.${left}`
    : `No grade still carries ${lineText(preview.old)}; nothing would change.${left}`;
  if (action === "remove") return count ? `${lineText(preview)} comes off ${plural(count, "grade")}.${left}` : `No grade still carries ${lineText(preview)}; nothing would change.${left}`;
  if (!count) return `No final grade of this question ${where} would change.`;
  return `${plural(count, "final grade")} ${where} ${count === 1 ? "gets" : "get"} ${lineText(preview)}.${left}`;
}

// The primary button's label: Preview until a preview matches what is typed, then what applying does.
export function submitLabel(preview) {
  const count = Number(preview?.counts?.change || 0);
  if (!preview || !count) return "Preview";
  const grades = plural(count, "grade");
  return preview.action === "edit" ? `Change on ${grades}` : preview.action === "remove" ? `Remove from ${grades}` : `Add to ${grades}`;
}

// The toast after an apply.
export function doneText(result) {
  if (result?.replay) return "This change was already made; nothing was changed twice.";
  const grades = plural(result?.changed, "grade");
  if (result?.action === "edit") return `Changed ${lineText(result.old)} to ${lineText(result)} on ${grades}.`;
  if (result?.action === "remove") return `Removed ${lineText(result)} from ${grades}.`;
  return `Added ${lineText(result)} to ${grades}.`;
}

// ------------------------------------------------------------------------------------------------------------------------------
// Markup

export function formMarkup(action, question, sections, batch, h) {
  const max = question?.max ?? "";
  if (action === "remove") {
    return `<p class="supporting-copy">Takes ${h.e(lineText(batch))} off the grades it was added to ${h.e(batch?.section_id === EVERY ? "in every section" : `in section ${batch?.section}`)} that still carry it. Each grade gets a review of yours without it; nothing is regraded.</p><div data-adjust-preview aria-live="polite"></div>`;
  }
  const fields = `<label class="field">The line's wording${seenByMarkup("students")}<input name="reason" data-adjust-input maxlength="${REASON_LIMIT}" autocomplete="off" required placeholder="Misprint in part (c)" value="${h.e(batch?.reason ?? "")}"></label>
    <label class="field">Points<input name="points" data-adjust-input type="number" step="0.01" inputmode="decimal" required placeholder="2" value="${h.e(batch?.points ?? "")}"></label>`;
  if (action === "edit") {
    return `<p class="supporting-copy">Changes ${h.e(lineText(batch))} on the grades it was added to ${h.e(batch?.section_id === EVERY ? "in every section" : `in section ${batch?.section}`)} that still carry it. A grade you changed by hand since is left alone.</p>
      <div class="certification-adjust-line">${fields}</div><p class="supporting-copy">Positive adds points, negative deducts${max !== "" ? `; the question is worth ${h.e(max)}` : ""}. The rubric does not change and nothing is regraded.</p><div data-adjust-preview aria-live="polite"></div>`;
  }
  // One section: it is the choice. Several: the instructor picks one (or every section), nothing is preselected.
  const only = sections.length === 1 ? sections[0].section_id : "";
  const options = [`<option value=""${only ? "" : " selected"} disabled>Choose a section</option>`,
    ...sections.map(s => `<option value="${h.e(s.section_id)}"${s.section_id === only ? " selected" : ""}>Section ${h.e(s.number)}</option>`),
    `<option value="${EVERY}">Every section</option>`].join("");
  return `<div class="certification-adjust-line">
    <label class="field">Section<select name="section" data-adjust-input required>${options}</select></label>${fields}</div>
    <p class="supporting-copy">Positive adds points, negative deducts${max !== "" ? `; the question is worth ${h.e(max)}` : ""}. Each final grade keeps its rubric lines and gets this line as a point adjustment in a review of yours. The rubric itself does not change, so nothing is regraded, and Gradescope receives the points as the grade's point adjustment and the wording as its comment.</p>
    <div data-adjust-preview aria-live="polite"></div>`;
}

export function previewMarkup(preview, {hideNames = false, error = null} = {}, h) {
  const failure = error ? `<div data-adjust-error>${calloutMarkup({tone: error.retry ? "warning" : "danger", title: error.title, body: error.body,
    action: error.retry ? {label: "Retry request", data: {adjustRetry: ""}} : null})}</div>` : "";
  if (!preview) return failure;
  const who = (row, i) => hideNames ? `Response ${i + 1}` : row.name;
  const changes = preview.changes || [], left = preview.left || [];
  const notes = row => [row.later_proposal ? "a newer proposal also waits for you" : "", ["sent", "changed"].includes(row.gradescope) ? "on Gradescope: send again" : ""].filter(Boolean).join(" · ");
  const changed = changes.length ? tableMarkup(h.e, [hideNames ? "Response" : "Student", "Section", "Now", "After", "Note"],
    changes.map((row, i) => `<tr data-adjust-change="${h.e(row.response_id)}"><th scope="row">${h.e(who(row, i))}</th><td>${h.e(row.section)}</td><td>${h.e(row.before)}</td><td>${h.e(row.after)}</td><td>${h.e(notes(row))}</td></tr>`),
    "Final grades that change") : "";
  const alone = left.length ? disclosureMarkup({label: "Left alone", hint: left.length, id: "adjust-left", open: !changes.length,
    body: tableMarkup(h.e, [hideNames ? "Response" : "Student", "Section", "Score", "Why"],
      left.map((row, i) => `<tr data-adjust-left="${h.e(row.response_id)}"><th scope="row">${h.e(who(row, changes.length + i))}</th><td>${h.e(row.section)}</td><td>${h.e(row.score ?? "–")}</td><td>${h.e(row.reason)}</td></tr>`),
      "Responses left alone")}) : "";
  const gradescope = Number(preview.counts?.on_gradescope || 0) ? `<p class="supporting-copy" data-adjust-gradescope>${h.e(`${plural(preview.counts.on_gradescope, "of these grades is", "of these grades are")} already on Gradescope; send them again from the Gradescope section afterwards.`)}</p>` : "";
  const certified = preview.certified ? `<p class="supporting-copy">These grades were certified: the step reopens until you certify again.</p>` : "";
  return `${failure}<p class="section-lead" data-adjust-summary>${h.e(summaryText(preview))}</p>${changed}${gradescope}${certified}${alone}`;
}

// ------------------------------------------------------------------------------------------------------------------------------
// The dialog

/**
 * @param {any} plan
 * @param {any} h grading helpers
 * @param {{base: string, question: any, sections?: {section_id: string, number: any}[], action?: "add" | "edit" | "remove", batch?: any,
 *   hideNames?: boolean, certified?: boolean, onBusy?: (busy: boolean) => void, onDone?: (result: any) => Promise<void>}} options
 */
export function openAdjustDialog(plan, h, {base, question, sections = [], action = "add", batch = null, hideNames = false, certified = false, onBusy = () => {}, onDone = async () => {}} = {}) {
  const title = `${question?.title || "Question"}${question?.version ? ` · Version ${question.version}` : ""}`;
  const url = `${base}/questions/${encodeURIComponent(question.question_id)}/adjustments`;
  const key = `${plan.id}:${question.question_id}:${action}:${batch?.batch_id || ""}`;
  const form = h.dialog(TITLES[action] || TITLES.add, title, formMarkup(action, question, sections, batch, h), "Preview", async () => { await submit(); });
  form.classList.add("certification-adjust-dialog");
  const dialog = form.closest("dialog");
  const live = () => form.isConnected && Boolean(dialog?.open);
  const host = form.querySelector("[data-adjust-preview]"), submitButton = form.querySelector('button[type="submit"]');
  if (action === "remove") { submitButton.classList.remove("primary"); submitButton.classList.add("danger"); }
  const state = {preview: null, previewed: "", error: null, requestId: ""};

  const typed = () => ({section: action === "add" ? form.elements.section.value : batch?.section_id,
    points: form.elements.points?.value ?? "", reason: form.elements.reason?.value ?? ""});
  const typedKey = () => JSON.stringify(requestBody(action, typed(), batch));
  function draw() {
    if (!live()) return;
    const open = new Set([...host.querySelectorAll("details[open]")].map(d => d.id).filter(Boolean));
    host.innerHTML = previewMarkup(state.preview, {hideNames, error: state.error}, h);
    open.forEach(id => host.querySelector(`#${CSS.escape(id)}`)?.setAttribute("open", ""));
    submitButton.textContent = state.error?.retry ? "Preview" : submitLabel(state.preview);
  }

  async function preview() {
    if (action !== "remove") {
      const problem = lineProblem(typed(), question?.max, action === "edit" ? batch : null);
      if (problem) throw new Error(problem);
    }
    state.error = null;
    const found = await h.api(`${url}/preview`, "POST", requestBody(action, typed(), batch));
    if (!live()) return;
    state.preview = {...found, certified}; state.previewed = typedKey(); state.requestId = "";
    draw();
  }

  async function deliver(payload) {
    let result;
    onBusy(true);
    try {
      result = await h.api(url, "POST", payload);
    } catch (error) {
      const status = Number(error.status || 0);
      if (status >= 400 && status < 500) {
        // A definite refusal: nothing was written. The preview no longer counts.
        unresolved.delete(key);
        state.preview = null; state.previewed = ""; state.requestId = "";
        state.error = {title: status === 409 ? "The grades changed since the preview" : "Nothing was changed", body: `${error.message} Nothing was changed.`};
        draw();
        return;
      }
      // No answer, or a server failure: the outcome is unknown. Keep the exact request for an explicit retry.
      unresolved.set(key, {body: payload});
      state.error = {title: "No answer came back", body: "The change may or may not have been made. Retry request sends the same change again: if it was recorded, nothing is changed twice.", retry: true};
      draw();
      return;
    } finally {
      onBusy(false);
    }
    unresolved.delete(key);
    if (live()) h.close();
    h.toast(doneText(result));
    await onDone(result);
  }

  async function submit() {
    if (!state.preview || state.previewed !== typedKey() || !Number(state.preview.counts?.change || 0)) { await preview(); return; }
    state.requestId ||= crypto.randomUUID();
    await deliver({...requestBody(action, typed(), batch), fingerprint: state.preview.fingerprint, request_id: state.requestId, confirmation: "adjust"});
  }

  form.addEventListener("input", event => {
    if (!event.target.matches?.("[data-adjust-input]")) return;
    // Another line is another request: the preview no longer describes it.
    if (state.preview && state.previewed !== typedKey()) { state.preview = null; state.requestId = ""; draw(); }
  });
  host.addEventListener("click", event => {
    if (!event.target.closest?.("[data-adjust-retry]")) return;
    const record = unresolved.get(key);
    if (record) { state.error = null; void runFormAction(form, () => deliver(record.body)); }
  });

  if (unresolved.has(key)) {
    const record = unresolved.get(key).body;
    if (form.elements.section && record.section_id) form.elements.section.value = record.section_id;
    if (form.elements.points && record.points !== undefined) form.elements.points.value = String(record.points);
    if (form.elements.reason && record.reason !== undefined) form.elements.reason.value = record.reason;
    state.error = {title: "An earlier request got no answer", body: "Retry request sends that same change again (nothing is changed twice), or Preview to start over.", retry: true};
    draw();
  } else if (action === "remove") void runFormAction(form, () => preview());
  else if (action === "edit") form.elements.reason.focus();
  else (form.elements.section.value ? form.elements.reason : form.elements.section).focus();
  return form;
}
