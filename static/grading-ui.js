import {runFormAction, dialogSession} from "./ui.js";
import {renderMath} from "./grading-math.js";
import {mountCropZoom} from "./grading-crops.js";

export const labels = {trial_accepted: "Trial-accepted · not final", auto_accepted: "Finalized automatically", stale: "Changed — review again",proposed: "Grade proposed", ocr_review: "Needs transcription review", grading_review: "Needs grading review", both: "Needs transcription and grading review", approved: "Approved", manual: "Manual grade", unreadable: "Unreadable", rejected: "Rejected", draft: "Review in progress", pending: "Queued", running: "Running", review_ready: "Ready for review", failed: "Failed", cancelled: "Cancelled", extracting: "Preparing response images", recognizing: "Reading handwriting", reconciling: "Checking the reading", grading: "Applying rubric", scoring: "Calculating score", complete: "Complete"};
export function helpers({api, escape, showDialog, dialogHeader, toast}) {
  const e = escape;
  const button = (key, label, extra = "") => `<button type="button" class="button" data-grading-action="${e(key)}" ${extra}>${e(label)}</button>`;
  // `hint`: markup under the label's words (`seenByMarkup`, ui.js); the control's name stays the label alone.
  const field = (label, name, value = "", type = "text", extra = "", hint = "") => `<label class="field">${e(label)}${hint}<input name="${e(name)}" aria-label="${e(label)}" type="${e(type)}" value="${e(value)}" ${extra}></label>`;
  const area = (label, name, value = "", extra = "", hint = "") => `<label class="field">${e(label)}${hint}<textarea name="${e(name)}" aria-label="${e(label)}" ${/\brows\s*=/.test(extra) ? "" : 'rows="5" '}${extra}>${e(value)}</textarea></label>`;
  const select = (label, name, choices, current = "") => `<label class="field">${e(label)}<select name="${e(name)}" aria-label="${e(label)}">${choices.map(([value, title]) => `<option value="${e(value)}" ${String(value) === String(current) ? "selected" : ""}>${e(title)}</option>`).join("")}</select></label>`;
  const check = (label, name, selected = false) => `<label class="grading-check"><input type="checkbox" name="${e(name)}" ${selected ? "checked" : ""}>${e(label)}</label>`;
  const error = '<p class="form-error" role="alert" hidden></p>';
  // The refusal sits in the footer beside the submit button, so a dialog taller than the screen still shows why its action did
  // nothing (an unticked confirmation used to be refused far below the fold).
  function dialog(title, subtitle, content, submit, operation) {
    showDialog(`<form id="grading-dialog">${dialogHeader(e(title), e(subtitle))}<div class="dialog-body grading-form">${content}</div><footer class="dialog-footer">${error}<button type="button" class="button" data-action="close">Close</button>${submit ? `<button class="button primary" type="submit">${e(submit)}</button>` : ""}</footer></form>`);
    const form = document.querySelector("#grading-dialog");
    renderMath(form);
    mountCropZoom(form);
    const active = dialogSession(form, document.querySelector("#dialog"));
    form.onsubmit = event => { event.preventDefault(); const values = new FormData(form); void runFormAction(form, () => operation(values, form, active)); };
    return form;
  }
  async function run(button, operation) {
    if (button.disabled) return;
    button.disabled = true;
    try { await operation(); } catch (error) { toast(error.message, true); } finally { if (button.isConnected) button.disabled = false; }
  }
  return {api, e, button, field, area, select, check, error, dialog, run, toast,
    close: () => document.querySelector("#dialog")?.close(),
    badge: status => `<span class="grading-badge grading-${e(status)}">${e(labels[status] || status)}</span>`};
}
