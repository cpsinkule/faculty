import {settingsView} from "./grading-settings.js";

// Keep the workspace settings draft outside the assignment DOM and the shared
// dialog, which is still used by the connection editor inside these settings.
export function createSettingsDialog(h, icon) {
  let dialog, host, view, trigger, child, generation = 0;
  const closeChild = () => {
    const owner = child?.closest("dialog");
    if (owner?.open) owner.close();
    child = null;
  };
  const close = () => { closeChild(); if (dialog?.open) dialog.close(); };
  window.addEventListener("hashchange", close);

  async function open(button) {
    if (dialog?.open) return;
    trigger = button;
    if (!dialog) {
      dialog = document.createElement("dialog");
      dialog.id = "grading-settings-dialog";
      dialog.className = "grading-settings-dialog";
      dialog.setAttribute("aria-labelledby", "grading-settings-title");
      dialog.innerHTML = `<header class="dialog-header"><div><h2 id="grading-settings-title">Compute settings</h2><p>Workspace defaults for new batches. Close and return without losing your draft.</p></div><button type="button" class="icon-button" data-settings-close aria-label="Close compute settings">${icon("close")}</button></header><div class="grading-settings-dialog-body"></div>`;
      host = dialog.querySelector(".grading-settings-dialog-body");
      dialog.querySelector("[data-settings-close]").onclick = close;
      dialog.addEventListener("close", () => {
        closeChild();
        view?.pause();
        // Ignore a load dismissed before the form was mounted.
        if (!view) generation++;
        if (trigger?.isConnected) trigger.focus({preventScroll: true});
      });
      document.body.append(dialog);
    }
    dialog.showModal();
    if (view?.isDirty() || host.querySelector('form[data-saving="true"]')) {
      view.resume();
      return;
    }
    view?.pause();
    view = null;
    const current = ++generation;
    const active = () => current === generation && dialog.isConnected;
    host.innerHTML = '<p class="loading-state">Loading compute settings…</p>';
    try {
      const mounted = await settingsView(host, {...h, dialog: (...args) => {
        child = h.dialog(...args);
        return child;
      }}, active);
      if (active()) view = mounted;
    } catch (error) {
      if (!active()) return;
      host.innerHTML = `<p role="alert">${h.e(error.message)}</p>${h.button("retry-settings", "Try again")}`;
      host.querySelector("button").onclick = () => {
        dialog.addEventListener("close", () => { void open(trigger); }, {once: true});
        dialog.close();
      };
    }
  }
  return {open};
}
