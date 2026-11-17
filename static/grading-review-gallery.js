// GRADING: one proposed-line group of the review queue, every response side by side (requirement, 2026-10-03). The proposed lines are shown once; each response shows its crops and, when a grading check held it, the check's reasons.
// The instructor sets aside or opens what they want to look at alone and approves the rest with the group's own lines. Each response
// still gets its own review (POST .../proposal-gallery/approve, checked again on the server). Responses held by a reading check, a
// rubric concern, graders that disagree or a finalization flag are shown greyed and are reviewed alone. The required tick is a
// native checkbox that clears whenever what is shown changes; no keyboard shortcut ticks it or approves (FRONTEND "Keyboard").
// BLANK ANSWERS (requirement, 2026-10-11): a response held only because the reader found no handwriting may be approved here too, and
// `only: "blank"` lists just those of the group. Nothing confirms a blank but the instructor's look at every crop. Since 2026-10-17
// (instructor: "it shouldn't show all of the models' commentary and only show the original scan crop, not both original and high
// contrast to save space") a blank tile shows the original crop alone, without the models' reasons; a greyed tile still says why.
import {cropButton, mountCropZoom} from "./grading-crops.js";
import {reviewReasonMarkup} from "./grading-model-comments.js";
import {renderMath} from "./grading-math.js";
import {loadingMarkup} from "./ui.js";

const PAGE = 24;
const count = (n, noun) => `${Number(n).toLocaleString()} ${noun}${n === 1 ? "" : "s"}`;

/** Draws the gallery into `work`. `lines` is the group's proposed lines (markup). Responses set aside here are reviewed alone next:
 * `onDone(aside)` names them, in order. */
export async function mountGallery(work, plan, h, {question, group, job = "", only = "", lines, active, onOpen, onDone, onClose}) {
  const blank = only === "blank";
  work.innerHTML = loadingMarkup(blank ? "Loading every possibly blank answer of this group…" : "Loading every response of this group…", 4);
  const url = `/api/grading/assignments/${plan.id}/proposal-gallery?${new URLSearchParams({question_id: question, group, ...(job ? {job_id: job} : {}), ...(only ? {only} : {})})}`;
  const gallery = await h.api(url);
  if (!active() || !work.isConnected) return;
  let shown = PAGE;
  const later = new Set();  // set aside in this gallery only
  const number = new Map(gallery.members.map((m, index) => [m.id, index + 1]));
  const draw = () => {
    const visible = gallery.members.slice(0, shown);
    const chosen = visible.filter(m => m.eligible && !later.has(m.id));
    const tile = m => {
      const aside = later.has(m.id);
      return `<figure class="grading-gallery-tile${m.eligible ? "" : " grading-gallery-alone"}${aside ? " grading-gallery-aside" : ""}${m.state === "proposed" ? "" : " grading-contact-flagged"}" data-gallery-member="${h.e(m.id)}">
        <figcaption><strong>Response ${h.e(number.get(m.id))}</strong>${!m.eligible ? ` <span class="supporting-copy">${h.e(m.why)}</span>` : aside ? ' <span class="supporting-copy">Set aside: you review it alone next</span>' : ""}</figcaption>
        ${m.images.map(src => cropButton(src, `Response ${number.get(m.id)} handwriting`, h)).join("")}
        ${!blank && m.reasons.length ? `<p class="grading-gallery-reasons">${m.reasons.map(r => reviewReasonMarkup(r, h)).join(" ")}</p>` : ""}
        <div class="grading-actions">${m.eligible ? `<button type="button" class="text-button" data-gallery-aside="${h.e(m.id)}">${aside ? "Include again" : "Set aside"}</button>` : ""}<button type="button" class="text-button" data-gallery-open="${h.e(m.id)}">Open alone</button></div></figure>`;
    };
    work.innerHTML = `<section class="grading-gallery" aria-label="Every response in this group">
      <header class="grading-gallery-head"><div><h4>${blank ? `${h.e(count(gallery.members.length, "possibly blank answer"))} in this group` : `All ${h.e(count(gallery.members.length, "response"))} in this group`}</h4><p class="supporting-copy">${blank ? "The reader found no handwriting on these. Look at every crop, and set aside or open any with writing (Open alone has its high-contrast view). The rest are confirmed blank with these lines, each with its own review." : "The AI proposed the same rubric lines for each. Look at every response; set aside or open any that does not fit. The rest are approved with these lines, each with its own review."}</p></div><button type="button" class="text-button" data-gallery-close>Back to one at a time</button></header>
      <div class="grading-gallery-lines">${lines}</div>
      <div class="grading-gallery-grid"${blank ? " data-gallery-blank" : ""}>${visible.map(tile).join("")}</div>
      ${gallery.members.length > shown ? `<p><button type="button" class="button" data-gallery-more>Show ${h.e(Math.min(PAGE, gallery.members.length - shown))} more</button> <span class="supporting-copy">Approval covers only the responses shown.</span></p>` : ""}
      <footer class="grading-gallery-approve">${chosen.length ? `<label class="grading-check"><input type="checkbox" data-gallery-inspected> ${blank ? "I looked at every crop shown here" : "I looked at every response shown here, including its work"}</label><button type="button" class="button primary" data-gallery-approve disabled>${blank ? `Confirm ${h.e(count(chosen.length, "blank answer"))}` : `Approve ${h.e(count(chosen.length, "response"))}`}</button>` : '<p class="supporting-copy">Nothing shown here can be approved together. Open each response alone.</p>'}</footer></section>`;
    renderMath(work);
    mountCropZoom(work);
    const tick = work.querySelector("[data-gallery-inspected]"), approve = work.querySelector("[data-gallery-approve]");
    if (tick) tick.onchange = () => { approve.disabled = !tick.checked; };
    work.querySelector("[data-gallery-close]").onclick = () => onClose();
    work.querySelector("[data-gallery-more]")?.addEventListener("click", () => { shown += PAGE; draw(); });
    work.querySelectorAll("[data-gallery-aside]").forEach(button => { button.onclick = () => {
      const id = button.dataset.galleryAside;
      if (later.has(id)) later.delete(id); else later.add(id);
      draw();  // What is shown changed: the tick clears with the redraw.
      work.querySelector(`[data-gallery-aside="${CSS.escape(id)}"]`)?.focus();
    }; });
    work.querySelectorAll("[data-gallery-open]").forEach(button => { button.onclick = () => onOpen(button.dataset.galleryOpen); });
    if (approve) approve.onclick = event => h.run(event.currentTarget, async () => {
      if (!tick.checked) return;
      const result = await h.api(`/api/grading/assignments/${plan.id}/proposal-gallery/approve`, "POST", {question_id: question, group, job_id: job || undefined, only: only || undefined,
        applied: gallery.applied, responses: chosen.map(m => ({id: m.id, version: m.version, result_id: m.result_id})), confirm_review: true, inspected_each: true});
      if (!active()) return;
      h.toast(`${count(result.approved, blank ? "blank answer" : "response")} ${blank ? "confirmed" : "approved"}${result.remaining ? `; ${result.remaining} still to review` : ""}.`);
      await onDone(gallery.members.filter(m => later.has(m.id)).map(m => m.id));
    });
  };
  draw();
}
