// Publishing grades on Gradescope (AGENTS.md "Sending grades to Gradescope", PUBLISHING GRADES; requirement, 2026-10-02). A section of the Gradescope fold on Certify, below the send
// panels: what was published and its Cancel/Retry. Publishing starts only from Certify's header (one place, 2026-10-18 review item 10). The server decides everything: its preview reads Gradescope and says per section which linked assignment
// (written grades, bubble sheets) would be published, what already is and why anything would not; Publish names that preview's
// fingerprint after one last popup; the background sender publishes each with Gradescope's own form and reads it back. This page
// shows, asks, and never publishes by itself. Faculty never unpublishes, and Gradescope emails nobody for publishing.
import {runFormAction, tableMarkup, whenVisible} from "./ui.js";
import {patchKeepingDisclosures} from "./grading-jobs.js";

// A preview reads every linked Gradescope course: far longer than the default request wait.
const WAIT = 180000;
const POLL = 4000;
const count = (n, one, many = `${one}s`) => `${Number(n || 0).toLocaleString()} ${Number(n) === 1 ? one : many}`;
const base = plan => `/api/grading/gradescope/assignments/${encodeURIComponent(plan.id)}`;

export const PARTS = {written: "Written grades", sheets: "Bubble sheets"};
export const publicationLive = publication => ["pending", "running"].includes(publication?.status);
export const publishUnitWords = {pending: "waiting", sending: "publishing", uncertain: "checking Gradescope", accepted: "published",
  present: "was already published", failed: "not published", cancelled: "cancelled"};
// Gradescope's graded share to one decimal, never rounded up to 100 or down to 0 (`gradescope_publish.percent`).
// An exact tie (a share of an odd number of quarters) goes to the even tenth as Python's round does; anything else to the nearest.
const tenth = x => {
  const tie = Number.isInteger(x * 4) && (x * 4) % 2 !== 0, t = Math.floor(x * 10);
  return tie ? (t % 2 === 0 ? t : t + 1) / 10 : Number(x.toFixed(1));
};
export const percent = share => `${Number(share) < 100 && Number(share) >= 99.95 ? 99.9 : Number(share) > 0 && Number(share) < 0.05 ? 0.1 : tenth(Number(share))}%`;
const sectionsText = numbers =>`${numbers.length === 1 ? "Section" : "Sections"} ${numbers.join(", ")}`;

// What the latest publication did, per Gradescope assignment.
export function publicationMarkup(publication, h, configured = true) {
  if (!publication) return "";
  const units = publication.units || [];
  const failed = units.filter(u => u.status === "failed").length;
  const done = units.filter(u => ["accepted", "present"].includes(u.status)).length;
  const heading = publication.status === "cancelled" ? "Cancelled" : publicationLive(publication) ? "Publishing"
    : failed ? `Not published in ${count(failed, "assignment")}` : "Published";
  const when = publication.created_at ? new Date(publication.created_at).toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"}) : "";
  const actions = [publicationLive(publication) ? h.button("gradescope-publish-cancel", "Cancel") : "",
    failed && publication.status !== "cancelled" && configured ? h.button("gradescope-publish-retry", `Retry (${failed})`) : ""].filter(Boolean).join("");
  return `<article class="grading-gradescope-send" data-gradescope-publication="${h.e(publication.id)}" aria-label="${h.e(`Publishing grades on Gradescope${when ? `, ${when}` : ""}`)}">
    <header><span class="grading-batch-caption">Latest publication${when ? ` · ${h.e(when)}` : ""}</span><h4>${h.e(heading)}</h4></header>
    ${publication.plan?.automatic ? `<p class="supporting-copy" data-gradescope-automatic>${publication.plan.automatic.after === "late" ? "Published by Faculty by itself, after it certified and sent a late paper." : "Published by Faculty by itself, after you certified."}</p>` : ""}
    ${units.length > 1 ? `<p class="supporting-copy">${h.e(`${done} of ${count(units.length, "assignment")} published`)}</p>` : ""}
    <ul class="grading-gradescope-links">${units.map(u => `<li><strong>${h.e(sectionsText(u.sections || []))}</strong> <span>${h.e(PARTS[u.part] || u.part)} → ${h.e(u.title)}${u.course ? ` (${h.e(u.course)})` : ""}</span> <span data-gradescope-state="${h.e(u.status)}">· ${h.e(publishUnitWords[u.status] || u.status)}</span>${u.error && u.status !== "present" ? `<p class="${u.status === "failed" ? "grading-warning" : "supporting-copy"}" role="status">${h.e(u.error)}</p>` : ""}</li>`).join("")}</ul>
    ${actions ? `<div class="grading-actions">${actions}</div>` : ""}
  </article>`;
}

export function publishPanelMarkup(status, h) {
  const configured = Boolean(status?.configured);
  const latest = (status?.publications || [])[0];
  const busy = publicationLive(latest);
  return `<h3 id="gradescope-publish-title">Publishing on Gradescope</h3>
    <p class="section-lead">When the grades on Gradescope are final, publish them there so students can see their grades and graded papers.</p>
    <details class="disclosure grading-gradescope-rules" data-gradescope-fold="publish-rules"><summary><span>How publishing works</span></summary><div class="disclosure-body"><p class="supporting-copy">Faculty publishes only when you ask, and only the Gradescope assignments your sections are linked to (written grades and bubble sheets). You see a preview first, can leave sections out, and confirm once more. Gradescope emails nobody for publishing, and Faculty never unpublishes: do that on Gradescope if you need to.</p></div></details>
    ${configured ? `<p class="supporting-copy" data-gradescope-publish-where>Publish with Publish grades on Gradescope… at the top of this page.</p>${busy ? '<p class="supporting-copy" id="gradescope-publish-busy">Faculty is publishing. A new preview can be looked at; the next publication waits until this one finishes.</p>' : ""}`
      : '<p class="supporting-copy" role="status">Gradescope is not set up for this Faculty (see above).</p>'}
    ${publicationMarkup(latest, h, configured)}`;
}

// --- the preview ------------------------------------------------------------------------------------------------------------
function stateMarkup(row, h) {
  if (row.left_out) return '<span class="chip">Left out</span>';
  if (row.problem) return `<span class="grading-warning">${h.e(row.problem)}</span>`;
  if (row.published) return '<span class="chip" data-tone="success">Published</span>';
  if (row.partly) return '<span class="chip" data-tone="warning">Partly published</span> <span class="chip" data-tone="info">Will be published</span>';
  return '<span class="chip" data-tone="info">Will be published</span>';
}

export function publishRowsMarkup(preview, h) {
  const rows = preview.rows || [];
  if (!rows.length) return '<p class="grading-gradescope-empty">No section of this assignment is linked to Gradescope yet: send to Gradescope first.</p>';
  // A row can be left out (or put back) only when publishing it is otherwise possible; one already published there has nothing to do.
  const choosable = row => row.left_out || (!row.problem && !row.published && row.target_id);
  return tableMarkup(h.e, ["Publish", "Section", "Gradescope assignment", "On Gradescope", "Submissions", "Graded there"], rows.map(row => {
    const letters = (row.versions || []).map(v => v.letter).filter(Boolean);
    const box = choosable(row) ? `<input type="checkbox" data-gradescope-publish-row="${h.e(row.key)}" ${row.left_out ? "" : "checked"} aria-label="${h.e(`Publish ${PARTS[row.part] || row.part} of section ${row.section}`)}">` : "";
    return `<tr data-gradescope-publish-key="${h.e(row.key)}"><td>${box}</td><td>${h.e(row.section)}<br><small>${h.e(PARTS[row.part] || row.part)}</small></td>
      <td>${row.title ? h.e(row.title) : "—"}${letters.length ? ` <small>(versions ${h.e(letters.join(", "))})</small>` : ""}${row.course ? `<br><small>${h.e(row.course)}</small>` : ""}</td>
      <td>${stateMarkup(row, h)}${row.warning && !row.left_out ? `<p class="grading-warning" role="status">${h.e(row.warning)}</p>` : ""}</td>
      <td>${row.left_out || row.problem ? "" : h.e(Number(row.submissions || 0).toLocaleString())}</td><td>${row.left_out || row.problem || row.graded === null || row.graded === undefined ? "" : h.e(percent(row.graded))}</td></tr>`;
  }), "What would be published on Gradescope");
}

export function publishPreviewMarkup(preview, h) {
  const parts = [];
  if (preview.running) parts.push('<p class="grading-warning" role="status">Faculty is still publishing this assignment’s grades. Let it finish, or cancel it, before publishing again.</p>');
  if ((preview.problems || []).length) parts.push(`<div class="callout grading-warning" data-tone="warning" role="status" data-gradescope-publish-problems><p><strong>Before Faculty can publish:</strong></p><ul>${preview.problems.map(p => `<li>${h.e(p)}</li>`).join("")}</ul></div>`);
  parts.push(`<h4 class="grading-gradescope-heading">Where</h4>${publishRowsMarkup(preview, h)}`);
  if ((preview.warnings || []).length) parts.push(`<div class="callout" data-tone="warning" data-gradescope-publish-warnings><p><strong>Before you publish, note:</strong></p><ul class="grading-gradescope-notes">${preview.warnings.map(w => `<li>${h.e(w)}</li>`).join("")}</ul></div>`);
  if (preview.ready) parts.push(`<div class="grading-gradescope-confirm">${h.check(`I checked this. Faculty publishes ${count(preview.publish, "Gradescope assignment")}; students then see their grades there.`, "understood")}</div>`);
  else if (!(preview.problems || []).length && !preview.running && (preview.rows || []).length) parts.push('<p class="grading-gradescope-empty" role="status">Nothing to publish: every linked assignment is already published, left out, or cannot be published (see above).</p>');
  return parts.join("");
}

// --- the last question ----------------------------------------------------------------------------------------------------
export function publishConfirmMarkup(preview, h) {
  const rows = (preview.rows || []).filter(r => !r.left_out && !r.problem && !r.published && r.target_id);
  const n = Number(preview.publish || 0);
  return `<header class="dialog-header"><div><h2 id="gradescope-publish-confirm-title">Publish ${h.e(count(n, "assignment"))} on Gradescope?</h2><p>Students see their grades and graded papers there as soon as it is done.</p></div></header>
    <div class="dialog-body"><ul class="grading-gradescope-published">${rows.map(r => `<li><strong>Section ${h.e(r.section)}</strong> → ${h.e(r.title)} <span>(${h.e(PARTS[r.part] || r.part)}, ${h.e(count(r.submissions, "submission"))})</span></li>`).join("")}</ul>
    ${(preview.warnings || []).length ? `<ul class="grading-gradescope-notes">${preview.warnings.map(w => `<li>${h.e(w)}</li>`).join("")}</ul>` : ""}
    <p class="supporting-copy">Gradescope emails nobody for publishing. Faculty never unpublishes: to hide the grades again, unpublish them on Gradescope.</p></div>
    <footer class="dialog-footer"><button type="button" class="button" data-publish-cancel>Cancel</button><button type="button" class="button danger" data-publish-confirm>Publish grades</button></footer>`;
}

// The popup the three last questions share (publish, send to a published exam, replace grades): resolves true only for its
// confirm button. Escape, Cancel and closing the parent dialog all answer no. `name` prefixes its data-NAME-cancel / -confirm buttons.
export function confirmPopup(markup, labelledBy, name, parent = null) {
  return new Promise(resolve => {
    const dialog = document.createElement("dialog");
    dialog.className = "grading-gradescope-published-dialog";
    dialog.setAttribute("aria-labelledby", labelledBy);
    dialog.innerHTML = markup;
    let answer = false;
    const close = () => { if (dialog.open) dialog.close(); };
    dialog.querySelector(`[data-${name}-cancel]`).onclick = close;
    dialog.querySelector(`[data-${name}-confirm]`).onclick = () => { answer = true; close(); };
    parent?.addEventListener("close", close);
    dialog.addEventListener("close", () => {
      parent?.removeEventListener("close", close);
      dialog.remove();
      resolve(answer);
    }, {once: true});
    document.body.append(dialog);
    dialog.showModal();
    dialog.querySelector(`[data-${name}-cancel]`).focus();
  });
}

// Like the send dialog's popups: true only for its confirm button. It is the question, not the control (the server checks the fingerprint).
export function confirmPublish(preview, h, parent = null) {
  return confirmPopup(publishConfirmMarkup(preview, h), "gradescope-publish-confirm-title", "publish", parent);
}

// --- the dialog -------------------------------------------------------------------------------------------------------------
export function publishDialog(plan, h, onPublished) {
  let preview = null, asked = [], requestId = "", generation = 0, loading = false, publishing = false;
  const leaveOut = new Set();
  const form = h.dialog("Publish grades on Gradescope", "Faculty reads Gradescope and shows what it would publish. Nothing is published until you tick the box, publish and confirm.",
    '<div data-gradescope-publish-outlook aria-live="polite"></div>', "Publish grades", async () => publish());
  form.classList.add("grading-batch-form");
  const active = () => form.isConnected && form.closest("dialog")?.open;
  const outlook = form.querySelector("[data-gradescope-publish-outlook]"), error = form.querySelector(".form-error");
  const submit = form.querySelector('button[type="submit"]');
  const blocked = () => publishing ? "Publishing…" : loading || !preview ? "Checking Gradescope…" : !preview.configured ? "Gradescope is not set up."
    : !preview.ready ? "There is nothing to publish." : "";
  const controls = () => {
    const reason = blocked();
    submit.disabled = Boolean(reason); submit.title = reason;
    form.querySelectorAll("[data-gradescope-publish-row]").forEach(box => { box.disabled = loading || publishing; });
  };

  async function load(notice = "") {
    const token = ++generation;
    asked = [...leaveOut].sort();
    preview = null; loading = true; requestId = "";
    error.hidden = true; error.textContent = "";
    outlook.innerHTML = `${notice}<p class="loading-state" role="status">Reading Gradescope and Faculty…</p>`;
    form.setAttribute("aria-busy", "true");
    controls();
    try {
      const result = await h.api(`${base(plan)}/publish/preview`, "POST", {leave_out: asked}, {}, WAIT);
      if (!active() || token !== generation) return;
      preview = result; requestId = crypto.randomUUID();
      outlook.innerHTML = notice + (result.configured ? publishPreviewMarkup(result, h)
        : `<div class="grading-warning" role="status"><p><strong>Gradescope is not set up for this Faculty.</strong> ${h.e(result.problem || "")}</p></div>`);
    } catch (cause) {
      if (active() && token === generation) outlook.innerHTML = `${notice}<p role="alert">${h.e(cause.message)}</p><p class="supporting-copy">Nothing was published. Close and open this again to read Gradescope once more.</p>`;
    } finally {
      if (token === generation) { loading = false; form.removeAttribute("aria-busy"); if (active()) controls(); }
    }
  }

  async function publish() {
    if (blocked()) throw new Error(blocked());
    if (!form.elements.understood?.checked) throw new Error("Tick the box to confirm you checked what Faculty will publish.");
    const shown = preview, id = requestId, leave = asked;
    if (!await confirmPublish(shown, h, form.closest("dialog"))) return;
    if (!active() || preview !== shown) return;  // the dialog closed or the preview changed while the popup was open
    publishing = true;
    controls();
    let receipt;
    try {
      // The same request ID for the same preview: publishing again after an unanswered request returns what the server recorded.
      receipt = await h.api(`${base(plan)}/publish`, "POST", {request_id: id, confirmation: "publish", fingerprint: shown.fingerprint, leave_out: leave}, {}, WAIT);
    } catch (cause) {
      const status = Number(cause.status || 0);
      if (status >= 400 && status < 500) {
        if (active()) void load(`<p class="grading-warning" role="status">${h.e(cause.message)} Nothing was published. Check the new preview.</p>`);
        return;
      }
      throw new Error("Faculty could not confirm whether this was recorded. Publish again asks with the same request: it returns what was recorded, or records it once.");
    } finally {
      publishing = false;
      if (active()) controls();
    }
    if (active()) h.close();
    h.toast(receipt?.status === "cancelled" ? "That was already recorded and cancelled." : "Faculty is publishing the grades on Gradescope. Follow it in the Gradescope section.");
    onPublished?.();
  }

  form.onsubmit = event => { event.preventDefault(); void runFormAction(form, publish).then(() => { if (active()) controls(); }); };
  outlook.addEventListener("change", event => {
    const box = event.target.closest?.("[data-gradescope-publish-row]");
    if (!box || loading || publishing) return;
    if (box.checked) leaveOut.delete(box.dataset.gradescopePublishRow);
    else leaveOut.add(box.dataset.gradescopePublishRow);
    void load();
  });
  void load();
  return form;
}

// --- the section ------------------------------------------------------------------------------------------------------------
// Resolves to {refresh}: the certification page's header opens `publishDialog` and asks the section to read again after it.
export async function mountPublish(host, plan, h, active) {
  if (!host || !plan) return;
  let timer = null, updating = false, again = false, status = null, written = "";  // `written`: the markup the panel holds (QUIET REFRESH)
  const running = () => active() && host.isConnected;
  host.innerHTML = '<section class="grading-panel grading-gradescope-publish" aria-labelledby="gradescope-publish-title"><h3 id="gradescope-publish-title">Publishing on Gradescope</h3><p class="loading-state" role="status">Reading what was published…</p></section>';
  const panel = host.querySelector(".grading-gradescope-publish");

  async function update() {
    if (updating) { again = true; return; }
    updating = true;
    clearTimeout(timer);
    try {
      try {
        status = await h.api(`${base(plan)}/publications`);
      } catch (cause) {
        written = "";
        if (running()) panel.innerHTML = `<h3 id="gradescope-publish-title">Publishing on Gradescope</h3><p role="alert">Faculty could not load what was published: ${h.e(cause.message)}</p><div class="grading-actions">${h.button("gradescope-publish-reload", "Try again")}</div>`;
        return;
      }
      if (!running()) return;
      // A poll that reads the same status changes nothing; a changed one patches in place, keeping the folds the instructor opened and
      // the focus (FRONTEND.md "Quiet refresh contract"; certify-3).
      const markup = publishPanelMarkup(status, h);
      if (markup !== written) {
        written = markup;
        patchKeepingDisclosures(panel, markup, node => node.dataset.gradescopeFold || "");
      }
    } finally {
      updating = false;
      if (running() && again) timer = setTimeout(() => { if (running()) void update(); }, 0);
      else if (running() && (status?.publications || []).some(publicationLive)) timer = setTimeout(() => whenVisible(() => { if (running()) void update(); }), POLL);
      again = false;
    }
  }

  // Delegated, so a redraw keeps them: the panel's markup is replaced on every read.
  panel.addEventListener("click", event => {
    const button = event.target.closest?.("[data-grading-action]");
    if (!button || button.disabled) return;
    const action = button.dataset.gradingAction;
    const id = button.closest("[data-gradescope-publication]")?.dataset.gradescopePublication;
    if (action === "gradescope-publish-reload") void update();
    else if (action === "gradescope-publish-cancel" && id) void h.run(button, async () => {
      if (!window.confirm("Stop publishing? Assignments still waiting are not published. What Gradescope already published stays published.")) return;
      await h.api(`${base(plan)}/publications/${encodeURIComponent(id)}/cancel`, "POST", {});
      h.toast("Stopped. Nothing more is published.");
      if (running()) await update();
    });
    else if (action === "gradescope-publish-retry" && id) void h.run(button, async () => {
      await h.api(`${base(plan)}/publications/${encodeURIComponent(id)}/retry`, "POST", {});
      h.toast("Publishing again where it did not go through.");
      if (running()) await update();
    });
  });
  await update();
  return {refresh: () => update()};
}
