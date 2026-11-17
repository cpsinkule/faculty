// What the local worker is doing for this assignment (requirement, 2026-09-25): papers whose versions are confirmed (page 1 read, else the whole paper compared with the blank
// exams), and blank pages read for the answer areas. Shown under the assignment's heading on every step. Counts come from the server and refresh while anything is under way;
// there is no time estimate. The two take turns on the one local worker.

import {whenVisible} from "./ui.js";

const POLL = 5000;

function progressRow(h, label, done, total, unit, note = "") {
  const percent = total ? Math.floor(done / total * 100) : 0;
  return `<div class="grading-local-work-row"><div class="grading-batch-progress"><p>${h.e(label)}: <b>${h.e(done)}</b> of ${h.e(total)} ${h.e(unit)}</p><span>${h.e(percent)}%</span></div>
    <progress max="${h.e(Math.max(1, total))}" value="${h.e(done)}" aria-label="${h.e(label)}"></progress>${note ? `<p class="supporting-copy">${h.e(note)}</p>` : ""}</div>`;
}

function markup(work, h) {
  const v = work.versions, rows = [];
  if (v.left) {
    rows.push(progressRow(h, "Confirming each paper’s version", v.done, v.total, "papers",
      v.failed ? `${v.failed} could not be confirmed; ${v.failed === 1 ? "it waits" : "they wait"} for your decision under Assignment versions.` : ""));
  }
  for (const t of work.templates) {
    rows.push(progressRow(h, `Reading ${t.label} for answer areas`, t.read, t.pages, "pages",
      `${t.questions} ${t.questions === 1 ? "question waits" : "questions wait"} on it.`));
  }
  if (!rows.length) return "";
  const shared = rows.length > 1 ? " These take turns, so each goes at about half speed while both run." : "";
  return `<aside class="grading-local-work" aria-live="polite"><p><strong>Running on the local worker.</strong>${shared}</p>${rows.join("")}</aside>`;
}

// onFinished(force): called once when the work this page watched has finished; it returns true when it refreshed the step itself
// (always with force, from the notice's own button).
// Otherwise (a dialog, typing or unsaved work on the page) the notice offers the refresh.
export function mountLocalWork(host, plan, h, active, {onFinished = () => false} = {}) {
  let running = false;
  async function refresh() {
    if (!active() || !host.isConnected) return;
    let work;
    try {
      work = await h.api(`/api/grading/assignments/${plan.id}/local-work`);
    } catch {
      if (running) setTimeout(() => whenVisible(refresh), POLL * 3);  // Keep what is shown; try again a little later.
      return;
    }
    if (!active() || !host.isConnected) return;
    if (work.active) {
      running = true;
      host.innerHTML = markup(work, h);
      setTimeout(() => whenVisible(refresh), POLL);  // a hidden tab reads once it is shown again
    } else if (running) {
      running = false;
      if (onFinished()) return;
      host.innerHTML = `<aside class="grading-local-work" aria-live="polite"><p><strong>Background work finished.</strong> Refresh when you are ready to see the results.</p><button type="button" class="button" data-local-work-refresh>Refresh now</button></aside>`;
      host.querySelector("[data-local-work-refresh]").onclick = () => { onFinished(true); };
    }
  }
  void refresh();
}
