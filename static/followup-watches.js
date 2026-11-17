// TEACHING-WORKFLOWS: enrollment-owned watches, explicitly reviewed before saving.
import { dialogSession, runFormAction } from "./ui.js";
import { campusToday } from "./calendar-feed.js";

export function watchesMarkup(s, e) {
  const states = {active: 'Watching', paused: 'Paused', error: 'Needs attention', triggered: 'Checked'};
  const rows = (s.watches || []).map(w => `<li><strong>${e(w.title)}</strong> · ${e(states[w.state] || w.state)}${w.check_back ? ` · Check back ${e(w.check_back)}` : ""}
    ${w.detail?.message ? `<p class="field-hint">${e(w.detail.message)}</p>` : ""}
    ${["active", "paused", "error"].includes(w.state) ? `<button type="button" class="text-button" data-followup-watch-state="${w.state === "active" ? "paused" : "active"}" data-watch="${e(w.id)}">${w.state === "active" ? "Pause" : "Resume"}</button>` : ""}
    <button type="button" class="text-button" data-followup-watch-state="removed" data-watch="${e(w.id)}">${w.state === "triggered" ? "Dismiss" : "Remove"}</button></li>`).join("");
  if (!s.watchable && !rows) return "";
  return `<section class="support-watches" data-patch-key="watches"><h4>Watch until done</h4>${rows ? `<ul>${rows}</ul>` : ""}
    ${s.watchable ? '<button type="button" class="text-button" data-followup-watch>Add a watch…</button>' : ""}</section>`;
}

export async function openWatch(item, ctx, e, binding = {}) {
  const dialog = document.getElementById("dialog"), previous = dialog.firstElementChild, wasOpen = dialog.open;
  const data = await ctx.api(`/api/student-support/followups/watch-options/${encodeURIComponent(item.enrollment_id)}`);
  if (dialog.firstElementChild !== previous || dialog.open !== wasOpen) return;
  ctx.showDialog(`<form id="followup-watch">${ctx.dialogHeader("Watch until done", item.title)}<div class="dialog-body">
    <p>Prepare one fresh review when a record changes or the check-back date arrives. Sending still needs your approval.</p>
    <label class="field">Watch for<select name="kind">${Object.entries(data.kinds).map(([id, label]) => `<option value="${e(id)}"${id === 'date' ? ' selected' : ''}>${e(label)}</option>`).join("")}</select></label>
    <label class="field" data-watch-target>Record<select name="target"></select></label>
    <label class="field">Check back on<input type="date" name="check_back" min="${campusToday()}" value="${e(binding.check_back || "")}"></label>
    <p class="field-hint">Checks local records every two minutes. Pausing automatic suggestions also pauses watches.</p>
    <p class="callout" data-watch-preview hidden></p><p class="form-error" role="alert" hidden></p></div>
    <footer class="dialog-footer"><button type="button" class="button" data-action="close">Cancel</button><button type="submit" class="button primary">Review watch</button></footer></form>`);
  const form = document.getElementById("followup-watch"), alive = dialogSession(form, dialog);
  const kind = form.elements.namedItem("kind"), target = form.elements.namedItem("target"), day = form.elements.namedItem("check_back");
  const submit = form.querySelector('[type="submit"]'), preview = form.querySelector("[data-watch-preview]");
  let reviewed = null, requestId = null, generation = 0;
  function reset() { generation++; reviewed = null; requestId = null; preview.hidden = true; submit.textContent = "Review watch"; }
  function choose() {
    const targets = data.targets[kind.value] || [];
    target.innerHTML = targets.map(t => `<option value="${e(t.id)}">${e(t.label)}</option>`).join("");
    form.querySelector("[data-watch-target]").hidden = kind.value === "date";
    day.required = kind.value === "date";
    target.required = kind.value !== "date";
    submit.disabled = kind.value !== "date" && !targets.length;
    reset();
  }
  kind.onchange = choose; day.oninput = reset; target.onchange = reset; choose();
  form.onsubmit = event => {
    event.preventDefault();
    void runFormAction(form, async () => {
      if (reviewed) {
        await ctx.api("/api/student-support/followups/watches", "POST", {...reviewed, request_id: requestId});
        if (alive()) dialog.close();
        ctx.toast("Watch saved. A change will prepare one fresh review.");
        await ctx.refresh();
      } else {
        const body = {enrollment_id: item.enrollment_id, item_key: item.suggestions.item_key, kind: kind.value,
          target: {...(kind.value === "date" ? {} : {id: target.value}), ...(binding.id ? {commitment_id: binding.id} : {})}, check_back: day.value};
        const serial = generation;
        const result = await ctx.api("/api/student-support/followups/watches/preview", "POST", body);
        if (!alive() || serial !== generation) return;
        reviewed = {...body, fingerprint: result.fingerprint}; requestId = crypto.randomUUID();
        preview.textContent = `${result.title}${body.kind === "date" ? "" : `: ${target.selectedOptions[0].textContent}`}${body.check_back ? ` · Check back ${body.check_back}` : ""}. ${result.note}`;
        preview.hidden = false; submit.textContent = "Save watch";
      }
    });
  };
}
