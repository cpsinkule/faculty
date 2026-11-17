// GRADING: a navigation view of the existing term-owned review queue. All writes use the shared review desk.
// The Review step opens here (requirement, 2026-10-03): on the remembered or first waiting response,
// with the queue's progress in the rail, the assignment-wide lines (flag review, Failed to grade) above
// the workspace, and completed questions and the grade summary folded below it. With nothing waiting, the main area says what is next.
import {inPrintedOrder, versionTitle} from "./grading-guide.js";
import {reviewHref, queueUrl, questionQueueUrl, groupName, groupLines, rubricDoubts, breadcrumb, failedSection, scoreTableMarkup} from "./grading-review-queue.js";
import {reviewSession, reviewRows, rememberReview, matchesReason, nextReview, reviewStart} from "./grading-review-session.js";
import {mountReviewDesk, closeReviewDesk, responseReviewUrl} from "./grading-review-desk.js";
import {renderMath} from "./grading-math.js";
import {loadingMarkup, emptyMarkup, nextStepMarkup, calloutMarkup} from "./ui.js";
import {mountFlagReview} from "./grading-flag-review.js";
import {concernActionsMarkup, bindConcerns} from "./grading-review-concerns.js";
import {mountGallery} from "./grading-review-gallery.js";
import {reviewNext} from "./grading-overview.js";

const reasons = [["", "All reasons"], ["rubric", "Rubric concern"], ["reading", "Reading check"], ["blank", "Possibly blank"], ["grading", "Grading check"], ["blind", "Blind review"], ["decision", "Other decisions"]];
const detailsUrl = url => `${url}${url.includes("?") ? "&" : "?"}details=review`;
const count = (n, noun) => `${n.toLocaleString()} ${noun}${n === 1 ? "" : "s"}`;

export async function reviewWorkspace(host, plan, h, active, initial, address = () => {}, {open = "", onChanged = null} = {}) {
  const scope = {...initial}, batch = scope.job ? {job: scope.job} : {};
  const session = reviewSession(plan, scope);
  let queue = await h.api(detailsUrl(queueUrl(plan, scope)));
  if (!active()) return;
  // Entered without a question: the remembered response if it still waits, else the first waiting one in printed order.
  if (!scope.question) Object.assign(scope, reviewStart(plan, queue, scope, session.cursor) || {});
  let desk = null, generation = 0, current = "", busy = false, shownQuestion = "", shownGroup = "";
  // ENTERING REVIEW (requirement, 2026-10-11). The response Review opens by itself is chosen before the flag
  // review's read answers, so it can be one the flag review is ruling on or about to. `first` is that response, from the choice
  // until the instructor does anything here or that first read is in: if the read names it, and it is still the one on screen with
  // nothing typed or ticked, Review moves to the next one that is theirs. A response the address named stays open, as does any
  // response later reads name: only the first read can move what is on screen.
  let first = "";
  for (const kind of ["pointerdown", "keydown", "input"]) host.addEventListener(kind, () => { first = ""; }, {capture: true, once: true});
  const href = extra => reviewHref(plan, {...batch, ...extra});
  const question = () => plan.questions.find(q => q.question_id === scope.question);
  const entry = () => queue.questions.find(q => q.question_id === scope.question);
  const group = () => entry()?.groups.find(g => g.id === scope.group);
  const members = () => (group()?.members || []).filter(member => matchesReason(member, session.reason));
  // The group's responses the reader found no handwriting on: the gallery lists them together (requirement, 2026-10-11).
  const blankCount = () => (group()?.members || []).filter(member => (member.reason_kinds || []).includes("blank")).length;
  const leave = () => !busy && (!desk || desk.leave());
  const pruneLater = () => { const live = new Set(reviewRows(queue).map(row => row.id)); for (const id of session.later) if (!live.has(id)) session.later.delete(id); };
  pruneLater();
  const context = queue.batch ? `Batch · ${h.e(new Date(queue.batch.created_at).toLocaleString())}. Current results from this batch only. <a href="${h.e(reviewHref(plan))}">Review all batches →</a>` : "All batches · current responses for this assessment.";
  const wide = !scope.job;  // The flag review covers the whole assignment: assignment-wide view only.
  host.innerHTML = `<header class="grading-heading grading-stage-heading grading-review-heading"><div><h3>Review grades</h3><p class="section-lead" data-workspace-lead></p></div></header><div class="grading-review-band"><div class="grading-review-where"><div data-workspace-breadcrumb></div><p class="supporting-copy" data-review-batch>${context}</p></div>${wide ? '<div class="grading-flag-review-slot"></div>' : ""}<div data-workspace-failed></div></div><div class="grading-review-workspace"><details class="grading-review-navigation disclosure" ${window.matchMedia("(min-width: 1300px)").matches ? "open" : ""}><summary><span>Review queue</span><span data-workspace-total></span></summary><div class="disclosure-body"><div class="grading-review-progress" data-workspace-progress></div><div data-workspace-questions></div>${h.select("Filter by reason", "review-reason", reasons, session.reason)}<div data-workspace-members></div></div></details><section class="grading-review-main"><div data-workspace-context></div><p class="sr-only" role="status" data-workspace-status></p><div class="grading-review-work"></div></section></div><footer class="grading-review-footer" data-workspace-footer></footer>`;
  const work = host.querySelector(".grading-review-work");
  const say = text => { host.querySelector("[data-workspace-status]").textContent = text; };
  // Something changed the plan's counts (a decision saved or found changed, a gallery approval, a regrade or retry sent, a finished
  // flag review): the page's step graph follows by its own read of the plan (`onChanged`, grading.js `planFollower`), whatever this
  // workspace's read of its queue answers. Nothing is drawn here.
  const changed = () => { onChanged?.(); };
  const showError = (error, retry) => {
    work.innerHTML = `<div class="callout" data-tone="danger" role="alert"><strong class="callout-title">Could not load the review</strong><p>${h.e(error.message)}</p><button type="button" class="button" data-review-retry>Try again</button></div>`;
    work.querySelector("[data-review-retry]").onclick = retry;
  };

  function renderNavigation() {
    const failed = queue.failed?.total || 0;
    host.querySelector("[data-workspace-total]").textContent = `${queue.waiting.toLocaleString()} left${failed ? ` · ${failed.toLocaleString()} failed` : ""}`;
    host.querySelector("[data-workspace-lead]").innerHTML = `${queue.waiting ? `<b>${h.e(count(queue.waiting, "response"))}</b> ${queue.waiting === 1 ? "waits" : "wait"} for your review.` : "No graded response is waiting for your review."}${failed ? ` <b>${h.e(count(failed, "response"))}</b> failed to grade.` : ""}`;
    const responses = queue.questions.reduce((sum, e) => sum + (e.responses || 0), 0), finals = queue.questions.reduce((sum, e) => sum + (e.approved || 0), 0);
    host.querySelector("[data-workspace-progress]").innerHTML = responses ? `<progress class="grading-review-progress-bar" max="${h.e(responses)}" value="${h.e(finals)}" aria-label="${h.e(`${finals} of ${responses} responses final`)}"></progress><small>${h.e(finals.toLocaleString())} of ${h.e(responses.toLocaleString())} final · ${h.e(queue.waiting.toLocaleString())} to review</small>` : "";
    const versions = new Map();
    for (const q of inPrintedOrder(plan.questions)) {
      const e = queue.questions.find(item => item.question_id === q.question_id);
      if (!e || !(e.waiting || e.failed || e.regrade || e.not_graded)) continue;
      const target = e.groups.find(g => g.members.length);
      const link = target ? {question: q.question_id, group: target.id} : {question: q.question_id};
      const text = e.waiting ? `${e.waiting}<span class="sr-only"> to review</span>` : h.e(e.failed ? `${e.failed} failed` : e.regrade ? `${e.regrade} to grade again` : `${e.not_graded} not graded`);
      const title = q.exam_version ? `Version ${q.exam_version}` : "Questions";
      if (!versions.has(title)) versions.set(title, []);
      versions.get(title).push(`<li><a href="${h.e(href(link))}" ${q.question_id === scope.question ? 'aria-current="true"' : ""}><span>${h.e(q.content.title)}${e.doubted ? '<span class="grading-review-concern-mark" title="Rubric concern"><span class="sr-only"> · rubric concern</span></span>' : ""}</span><small>${text}</small></a></li>`);
    }
    host.querySelector("[data-workspace-questions]").innerHTML = [...versions].map(([title, rows]) => `<section><h4>${h.e(title)}</h4><ul>${rows.join("")}</ul></section>`).join("");
    const rows = members(), later = reviewRows(queue, scope.question).filter(row => session.later.has(row.id)).length;
    host.querySelector("[data-workspace-members]").innerHTML = !scope.question ? "" : `<p class="supporting-copy">${count(rows.length, "response")} shown in this group${later ? ` · ${later} left for later in this question` : ""}</p><ol class="grading-review-member-list">${rows.map((row, index) => `<li><button type="button" class="text-button" data-review-member="${h.e(row.id)}" ${row.id === current ? 'aria-current="true"' : ""}><span>Response ${index + 1}</span><small>${session.later.has(row.id) ? "Later" : session.review.has(row.id) ? "With the flag review" : h.e((row.reason_kinds || []).map(kind => reasons.find(([key]) => key === kind)?.[1]).filter(Boolean).join(" · "))}</small></button></li>`).join("")}</ol>`;
    host.querySelectorAll("[data-review-member]").forEach(button => { button.onclick = () => {
      if (!leave()) return;
      session.later.delete(button.dataset.reviewMember);
      session.review.delete(button.dataset.reviewMember);  // Opened by hand: this one is the instructor's to decide.
      void load(button.dataset.reviewMember);
    }; });
    host.querySelector("[data-workspace-breadcrumb]").innerHTML = scope.question ? breadcrumb(plan, question(), group() ? groupName(group(), question()?.rubric?.rules) : "Review", h, scope) : "";
  }

  function renderContext() {
    if (shownQuestion === scope.question && shownGroup === scope.group) return;
    shownQuestion = scope.question; shownGroup = scope.group;
    if (!scope.question) { host.querySelector("[data-workspace-context]").innerHTML = ""; return; }
    const e = entry(), q = question();
    // One row of group tools above the desk (requirement, 2026-10-17): the group, its gallery
    // buttons, and the proposed lines and shared concerns as folds that take a row of their own only when opened.
    const doubts = e?.rubric_doubts?.length ? `<details class="disclosure grading-review-shared" data-workspace-concerns><summary><span>Shared rubric concerns</span><span class="disclosure-hint">${count(e.doubted, "response")} affected</span></summary><div class="disclosure-body">${rubricDoubts(plan, e, h, {inline: true})}${concernActionsMarkup(e, h)}</div></details>` : "";
    const groups = (e?.groups || []).map(g => [g.id, `${groupName(g, q?.rubric?.rules)} · ${count(g.size, "response")}`]);
    host.querySelector("[data-workspace-context]").innerHTML = `<div class="grading-review-group-picker">${h.select("Proposed rubric group", "review-group", groups, scope.group)}${group()?.kind === "proposal" && group().size > 1 ? `<button type="button" class="button" data-review-gallery>See all ${h.e(group().size)} in this group</button>` : ""}${group()?.kind === "proposal" && blankCount() > 1 ? `<button type="button" class="button" data-review-gallery-blank>See the ${h.e(blankCount())} possibly blank answers</button>` : ""}${group() ? `<details class="disclosure grading-review-scope"><summary><span>Proposed rubric lines</span></summary><div class="disclosure-body">${groupLines(group(), h, q?.rubric?.rules)}</div></details>` : ""}${doubts}<a class="text-button" href="${h.e(href({question: scope.question}))}">Groups &amp; review tools</a></div>`;
    host.querySelector('[name="review-group"]').onchange = event => {
      if (!leave()) { event.target.value = scope.group; return; }
      scope.group = event.target.value;
      renderContext();
      void advance("", false);
    };
    host.querySelector("[data-review-gallery]")?.addEventListener("click", () => { if (leave()) void gallery(); });
    host.querySelector("[data-review-gallery-blank]")?.addEventListener("click", () => { if (leave()) void gallery("blank"); });
    const concerns = host.querySelector("[data-workspace-concerns]");
    if (concerns) bindConcerns(concerns, plan, e, h, {active, certified: plan.workflow?.certification?.state === "current",
      // The regraded responses leave the queue (they are pending again): read it, and move on if the one on screen was among them.
      onRegraded: async () => { if (!active()) return; changed(); await readQueue(); if (active() && !reviewRows(queue, scope.question).some(row => row.id === current) && (!desk || desk.leave())) await advance(); }});
    renderMath(host.querySelector("[data-workspace-context]"));
  }

  // Failed to grade, above the workspace: redrawn on whole reads, kept open if the instructor opened it.
  function renderFailed() {
    const slot = host.querySelector("[data-workspace-failed]"), wasOpen = slot.querySelector("[data-review-failed]")?.open;
    slot.innerHTML = failedSection(plan, queue.failed, h);
    if (wasOpen && slot.querySelector("[data-review-failed]")) slot.querySelector("[data-review-failed]").open = true;
    slot.querySelectorAll('[data-grading-action="retry-failed"]').forEach(button => button.addEventListener("click", event => h.run(event.currentTarget, async () => {
      const result = await h.api(`/api/grading/jobs/${encodeURIComponent(button.dataset.job)}/retry`, "POST", {});
      const skipped = result.retry?.skipped || [];
      h.toast(`${count(result.retry?.retried || 0, "response")} sent to be graded again.${skipped.length ? ` ${skipped.length} skipped: ${[...new Set(skipped.map(s => s.reason))].join(" ")}` : ""}`);
      if (!active()) return;
      changed();
      await readQueue(true);
      if (active() && !desk) await finish();
    })));
  }
  const showFailed = () => {
    const failed = host.querySelector("[data-review-failed]");
    if (!failed) return;
    failed.open = true;
    failed.scrollIntoView({block: "start"});
    failed.querySelector("summary").focus({preventScroll: true});
  };

  // Below the workspace: finished questions and the final grades, both folded, and the ways out.
  function renderFooter() {
    const footer = host.querySelector("[data-workspace-footer]");
    const opened = new Set([...footer.querySelectorAll("details[open]")].map(node => node.dataset.workspaceFold));
    const entries = new Map(queue.questions.map(e => [e.question_id, e]));
    const done = inPrintedOrder(plan.questions).filter(q => { const e = entries.get(q.question_id); return e?.responses && !(e.waiting || e.failed || e.regrade || e.not_graded); });
    const finals = queue.questions.reduce((sum, e) => sum + (e.approved || 0), 0), automatic = queue.questions.reduce((sum, e) => sum + (e.automatic || 0), 0);
    const spotCheck = automatic ? `<a class="text-button" data-spot-check-link href="${h.e(reviewHref(plan, {browse: "all", status: "automatic", spot: "10"}))}">Spot-check 10 automatic grades at random</a>` : "";
    const completed = done.length ? `<details class="disclosure" data-workspace-fold="completed" data-review-completed><summary><span>Completed questions</span><span class="disclosure-hint">${h.e(done.length)}</span></summary><div class="disclosure-body"><ul class="grading-review-completed">${done.map(q => { const e = entries.get(q.question_id); return `<li><a href="${h.e(href({question: q.question_id}))}"><strong>${h.e(versionTitle(q))}</strong> <small>No review needed · ${h.e(e.automatic.toLocaleString())} finalized automatically · ${h.e((e.approved - e.automatic).toLocaleString())} reviewed by you</small></a></li>`; }).join("")}</ul></div></details>` : "";
    const next = reviewNext(plan, queue);
    footer.innerHTML = `${completed}<details class="disclosure" data-workspace-fold="summary" data-review-grade-summary><summary><span>Grade summary</span><span class="disclosure-hint">${h.e(automatic.toLocaleString())} finalized automatically · ${h.e((finals - automatic).toLocaleString())} reviewed by you</span></summary><div class="disclosure-body">${scoreTableMarkup(plan, queue, h)}${spotCheck}</div></details><p class="grading-review-footer-links"><a class="text-button" href="${h.e(reviewHref(plan, {browse: "all"}))}">All responses, approved grades and review tools →</a><a class="text-button" href="${h.e(next.href)}">${h.e(next.label)} →</a></p>`;
    footer.querySelectorAll("details").forEach(node => { if (opened.has(node.dataset.workspaceFold)) node.open = true; });
  }

  // Nothing waits in the scope: the one next thing, from the server's counts.
  function emptyBody() {
    const failed = queue.failed?.total || 0;
    const finals = queue.questions.reduce((sum, e) => sum + (e.approved || 0), 0);
    if (failed) return calloutMarkup({tone: "warning", title: `No graded response is waiting for your review; ${count(failed, "response")} failed to grade`, body: "They have no grade yet. Read why above, then retry them or grade them in a new batch.", action: {label: `Review ${count(failed, "failed response")}`, data: {reviewShowFailed: ""}}});
    if (scope.job && queue.other_waiting) return nextStepMarkup({title: "This batch is reviewed", detail: `${count(queue.other_waiting, "response")} ${queue.other_waiting === 1 ? "waits" : "wait"} in other batches.`, label: "Review all batches", href: reviewHref(plan)});
    if (!plan.questions.length) return nextStepMarkup({title: "No questions yet", detail: "Upload the rubrics in Rubrics & Templates.", label: "Go to Rubrics & Templates", href: `#grading/${plan.id}?tab=rubrics`});
    if (queue.questions.some(e => e.regrade || e.not_graded)) return nextStepMarkup({title: "Some responses still need grading", detail: "Nothing graded is waiting for your review.", label: "Go to Grade", href: `#grading/${plan.id}?tab=jobs`});
    if (scope.job && !finals) return emptyMarkup({kind: "filtered", title: "No current responses from this batch to review yet"});
    return nextStepMarkup(reviewNext(plan, queue));
  }

  // Reads after a save retain one-question scope. A boundary read refreshes the whole queue before suggesting another question.
  async function readQueue(whole = false) {
    const answer = await h.api(detailsUrl(whole ? queueUrl(plan, scope) : questionQueueUrl(plan, scope)));
    if (!active()) return;
    if (!answer.question_id) { queue = answer; renderFailed(); renderFooter(); }
    else {
      const old = entry(), next = answer.questions.find(q => q.question_id === scope.question);
      queue.waiting += (next?.waiting || 0) - (old?.waiting || 0);
      queue.questions = queue.questions.filter(q => q.question_id !== scope.question);
      if (next) queue.questions.push(next);
    }
    pruneLater();
    // Update the shared concern counts without replacing an open disclosure on unchanged reads.
    const open = host.querySelector("[data-workspace-concerns]")?.open;
    shownQuestion = "";
    renderContext();
    if (open && host.querySelector("[data-workspace-concerns]")) host.querySelector("[data-workspace-concerns]").open = true;
  }

  async function finish() {
    ++generation;  // A filter can finish while a previous response read is still in flight.
    closeReviewDesk(); desk = null; current = "";
    renderNavigation();
    if (!scope.question) {
      work.innerHTML = emptyBody();
      work.querySelector("[data-review-show-failed]")?.addEventListener("click", showFailed);
      work.querySelector("button, .next-step a")?.focus({preventScroll: true});
      say(queue.failed?.total ? "Nothing waits for review; some responses failed to grade." : "No responses need review.");
      return;
    }
    const waiting = entry()?.waiting || 0;
    const filteredOut = session.reason && reviewRows(queue, scope.question).some(row => !matchesReason(row, session.reason));
    const later = reviewRows(queue, scope.question).filter(row => session.later.has(row.id));
    const ruled = reviewRows(queue, scope.question).filter(row => session.review.has(row.id));
    let body = "";
    if (filteredOut) body = emptyMarkup({kind: "filtered", title: "No more responses match this filter", body: `${count(waiting, "response")} still need review in this question.`, action: {label: "Show all reasons", data: {reviewClearReason: ""}}});
    else if (later.length) body = emptyMarkup({kind: "filtered", title: `${count(later.length, "response")} left for later`, body: "These responses are still pending and still count as work to review.", action: {label: "Review responses left for later", data: {reviewLater: ""}}});
    // The flag review rules on these, now or by itself in about two minutes (2026-10-11): what it settles never needs a look here.
    else if (ruled.length) body = emptyMarkup({kind: "filtered", title: `${count(ruled.length, "response")} with the flag review`, body: "It is ruling on these, or starts by itself about two minutes after the batch. What it cannot settle comes back here; they still count as work to review.", action: {label: "Review them yourself", data: {reviewRuled: ""}}});
    else body = `<p class="grading-review-done">No response in ${h.e(question() ? versionTitle(question()) : "this question")} is waiting for review.</p>`;
    const ordered = inPrintedOrder(plan.questions), at = ordered.findIndex(q => q.question_id === scope.question);
    const next = [...ordered.slice(at + 1), ...ordered.slice(0, Math.max(at, 0))].find(q => queue.questions.find(e => e.question_id === q.question_id)?.waiting);
    const target = next && queue.questions.find(e => e.question_id === next.question_id)?.groups[0];
    const trouble = queue.questions.some(q => q.failed || q.regrade || q.not_graded);
    const step = next ? {title: waiting ? "Continue with another question" : "Question complete", detail: `Next: ${versionTitle(next)}. Check its question and rubric before deciding.`, label: `Review ${versionTitle(next)}`, href: href({question: next.question_id, group: target.id})}
      : scope.job && queue.other_waiting ? {title: "Other batches need review", detail: `${count(queue.other_waiting, "response")} wait in other batches.`, label: "Review all batches", href: reviewHref(plan)}
      : trouble ? {title: "Some work still needs attention", detail: "Check failed responses and work that still needs grading.", label: "See what still needs attention", href: href({})}
      : !waiting ? reviewNext(plan, queue) : null;
    work.innerHTML = body + (step ? nextStepMarkup(step) : "");
    work.querySelector("[data-review-clear-reason]")?.addEventListener("click", () => { session.reason = ""; host.querySelector('[name="review-reason"]').value = ""; void advance(); });
    work.querySelector("[data-review-later]")?.addEventListener("click", () => { for (const row of later) session.later.delete(row.id); void advance(); });
    work.querySelector("[data-review-ruled]")?.addEventListener("click", () => { session.review.clear(); void advance(); });
    work.querySelector("button, .next-step a")?.focus({preventScroll: true});
    say(waiting ? `${waiting} responses still need review in this question.` : "Question complete.");
  }

  async function advance(preferred = "", boundaryRead = true) {
    let next = nextReview(queue, scope, session, preferred);
    if (!next && boundaryRead) { await readQueue(true); if (!active()) return; next = nextReview(queue, scope, session); }
    if (!next) { await finish(); return; }
    if (scope.group !== next.group) {
      scope.group = next.group;
      renderContext();
      h.toast(`Next rubric group: ${groupName(group(), question()?.rubric?.rules)}`);
    }
    await load(next.id);
  }

  // Every response of the group side by side (grading-review-gallery.js). The desk and its keys are closed first, so no key approves.
  async function gallery(only = "") {
    const token = ++generation;
    closeReviewDesk(); desk = null; current = "";
    renderNavigation();
    address({question: scope.question, group: scope.group, response: ""});
    try {
      await mountGallery(work, plan, h, {question: scope.question, group: scope.group, job: scope.job, only, lines: groupLines(group(), h, question()?.rubric?.rules),
        active: () => active() && token === generation,
        onOpen: id => { session.later.delete(id); session.review.delete(id); void load(id); },
        onClose: () => { void advance("", false); },
        onDone: async aside => { busy = true; changed(); try { await readQueue(); if (active()) await advance(aside[0] || ""); } finally { busy = false; } }});
      say(only === "blank" ? `The possibly blank answers of this group. ${count(blankCount(), "response")}.` : `Every response of this group. ${count(group()?.size || 0, "response")}.`);
    } catch (error) { if (active() && token === generation) showError(error, () => { void gallery(only); }); }
  }

  async function load(id, reveal = false) {
    const token = ++generation;
    closeReviewDesk(); desk = null;
    current = id;
    renderContext(); renderNavigation();
    if (!id) { await finish(); return; }
    address({question: scope.question, group: scope.group, response: id});
    work.innerHTML = loadingMarkup("Loading original work and rubric…", 5);
    try {
      // `details=review`: the desk's reasons gathered by cause (GRADING, 2026-10-11); nothing more for a hidden proposal.
      const value = await h.api(responseReviewUrl(id, reveal));
      if (!active() || token !== generation) return;
      rememberReview(plan, scope, {question: scope.question, group: scope.group, response: id});
      const rows = members(), index = rows.findIndex(row => row.id === id);
      const certification = plan.workflow?.certification;
      desk = mountReviewDesk(work, value, h, {index: Math.max(0, index), total: rows.length, groupSize: rows.length, active,
        left: entry()?.waiting ?? null, moved: true,
        certified: certification?.state === "current" ? certification.created_at || "current" : "",
        student: `/api/grading/assignments/${encodeURIComponent(plan.id)}/responses/${encodeURIComponent(id)}/student`,
        approveNext: true,
        onMove: step => { void load(rows[index + step]?.id || id); },
        onLater: () => { session.later.add(id); h.toast("Left for later. This response still needs review."); void advance("", false); },
        onReveal: () => { void load(id, true); },
        // GRADING "KEEP CONFLICTED EDITS": the desk keeps the edits; this response is read as it was shown and the queue around it.
        reread: () => h.api(responseReviewUrl(id, reveal)),
        onConflict: async () => { await readQueue(); if (active()) { renderNavigation(); renderFooter(); } },
        onSaved: status => refreshAfter(status, id, rows[index + 1]?.id || "")});
      const heading = work.querySelector("[data-review-heading]");
      if (heading) heading.textContent = `${question() ? versionTitle(question()) : "Review"} · Response ${index + 1}`;
      say(`${question() ? versionTitle(question()) : "Response"}. Response ${index + 1} of ${rows.length} in this group.`);
    } catch (error) { if (active() && token === generation) showError(error, () => { void load(id, reveal); }); }
  }

  async function refreshAfter(status, id, preferred = "") {
    busy = true;
    changed();
    try {
      await readQueue();
      if (!active()) return;
      if (["approved", "manual"].includes(status)) session.later.delete(id);
      const kept = reviewRows(queue, scope.question).find(row => row.id === id);
      if (status === "draft" && kept) { scope.group = kept.group; await load(id); }
      else if (["unreadable", "rejected"].includes(status) && kept) { session.later.add(id); await advance(preferred); }
      else await advance(preferred);
    } catch (error) {
      if (active()) {
        closeReviewDesk(); desk = null;
        showError(new Error(`${status ? "Your decision was saved, but the queue could not be refreshed. " : ""}${error.message}`), () => { void refreshAfter("", id, preferred); });
      }
    } finally { busy = false; }
  }

  host.querySelector('[name="review-reason"]').onchange = event => {
    if (!leave()) { event.target.value = session.reason; return; }
    session.reason = event.target.value;
    void advance("", false);
  };
  renderFailed(); renderFooter();
  if (wide) {
    // Gradescope regrade requests moved to Return › Regrade requests (REGRADES, 2026-10-07): a decision there reaches every destination.
    const flags = host.querySelector(".grading-flag-review-slot");
    // A finished flag review changes the counts: the rail and the footer are read again; the desk on screen is left alone.
    mountFlagReview(flags, plan, h, active, () => { changed(); if (!busy) void readQueue(true).then(() => { if (active()) renderNavigation(); }).catch(() => {}); },
      // The responses it is ruling on, or is about to by itself: stepped past and labelled, never hidden; the one open stays open,
      // unless it is the one Review itself opened on entry and nothing was done with it yet (ENTERING REVIEW above).
      ids => {
        session.review = new Set(ids);
        if (busy || !active()) return;
        if (first && first === current && session.review.has(current) && !desk?.dirty()) {
          first = "";
          address({question: scope.question, group: scope.group, response: ""});  // a reload must not name what was stepped past
          void advance("", false);
        } else renderNavigation();
      }).then(() => { first = ""; }, () => { first = ""; flags.remove(); });
  }
  const requested = members().find(row => row.id === scope.response);
  renderContext();
  if (requested) { first = wide && !initial.response ? requested.id : ""; await load(requested.id); }
  else { const going = advance(); first = wide ? current : ""; await going; }
  if (open === "failed" && active()) showFailed();
}
