/* TEACHING-WORKFLOWS: projections of certified evidence and delivery receipts. Return's third sub-tab, Regrade requests
   (REGRADES, 2026-10-07), is grading-regrades.js, mounted beside the patched page so its desk and typed reply survive a redraw. */
import {patchChildren} from "./dom-patch.js";
import {helpers} from "./grading-ui.js";
import {mountRegradePanel} from "./grading-regrades.js";

/**
 * EACH CANVAS SITUATION BY ITSELF (TEACHING-WORKFLOWS, requirement, 2026-10-11; Return said "N missing, changed or unconfirmed" in one
 * number): what Faculty's receipts say of an assessment's grades in Canvas, over its gradebook columns (the delivery read's
 * `columns`): accepted and still the grade here, never sent, changed here since Canvas accepted it, sent with no answer yet (a
 * later Sync's read confirms it) and failed. `open` is everything but accepted. Pure.
 * @param {any[]} columns
 */
export function canvasReceipts(columns) {
  const sum = (/** @type {(column: any) => number} */ of) => (columns || []).reduce((n, column) => n + (of(column) || 0), 0);
  const found = {accepted: sum(c => c.receipts?.accepted), never: sum(c => c.without_receipt), changed: sum(c => c.receipts?.changed),
    uncertain: sum(c => c.receipts?.uncertain), failed: sum(c => c.receipts?.failed)};
  return {...found, open: found.never + found.changed + found.uncertain + found.failed};
}

/** Return's "Sent to Canvas" line from `canvasReceipts`: every situation that has any, each with its own count. Pure. */
export function canvasSentText(columns) {
  const found = canvasReceipts(columns);
  const parts = [[found.accepted, 'accepted'], [found.never, 'never sent'], [found.changed, 'changed since sent'], [found.uncertain, 'unconfirmed'], [found.failed, 'failed']]
    .filter(([count]) => count).map(([count, words]) => `${count} ${words}`);
  return parts.join(' · ') || 'Nothing sent';
}

/**
 * POSTED IN CANVAS (TEACHING-WORKFLOWS, requirement, 2026-10-11): whether students see the grades in Canvas, from what the gradebook
 * last read of each column's posting there (the delivery read's `columns[].posting`, the grid's own "hidden" mark), with the time
 * of that read: nothing read yet, no grade there, every grade shown, some hidden, or all hidden. A read, never a claim about now;
 * a column not read yet is said beside the others. `when` formats the read's time. Pure.
 * @param {any[]} columns @param {(stamp: string) => string} [when]
 */
export function canvasPostedText(columns, when = String) {
  const read = (columns || []).map(column => column.posting).filter(Boolean), unread = (columns || []).length - read.length;
  if (!read.length) return 'Not read from Canvas yet';
  const graded = read.reduce((n, mark) => n + (mark.graded || 0), 0), hidden = read.reduce((n, mark) => n + (mark.hidden || 0), 0);
  const times = [...new Set(read.map(mark => mark.read_at).filter(Boolean))].sort((a, b) => Date.parse(a) - Date.parse(b));
  const freshness = times.length > 1 ? `read at different times: ${when(times[0])} to ${when(times.at(-1))}`
    : times.length ? `read ${when(times[0])}` : 'Read time not recorded';
  const grades = (/** @type {number} */ n) => `${n} grade${n === 1 ? '' : 's'}`;
  const seen = !graded ? 'No grades in Canvas' : !hidden ? `Students see ${graded === 1 ? 'the 1 grade' : `all ${graded} grades`}`
    : hidden === graded ? `${graded === 1 ? 'The 1 grade is' : `All ${graded} grades are`} hidden from students` : `${hidden} of ${grades(graded)} hidden from students`;
  return [seen, freshness, read.some(mark => !mark.read_at) && times.length ? 'some read times not recorded' : '', unread ? `${unread} column${unread === 1 ? '' : 's'} not read yet` : ''].filter(Boolean).join(' · ');
}

export function draftRequest(view, kind) {
  const weakest = view.questions.slice(0, 5).map(q => ({title: q.title, version: q.version,
    question_revision_id: q.question_revision_id, rubric_id: q.rubric_id, objectives: q.objectives,
    n: q.n, earned_pct: q.earned_pct, statement: q.statement.slice(0, 4000), rubric_lines: q.rubric_lines.slice(0, 15)}));
  return `Draft ${kind === "lesson" ? "a 20-minute review lesson with a worked example, guided practice and an exit ticket" : "a short practice set with worked solutions and a point-based rubric"} for ${view.title}.
Use the certified evidence below to choose priorities. Low scores do not establish a misconception; distinguish observations from hypotheses. Respect the sample sizes and separate versions. Produce a draft in this chat for my review; do not change grades, create print jobs, publish or send anything. You may read the course's existing notes and bank through Faculty if needed.
Evidence source: /api/teaching-review/${view.plan_id}; certification ${view.certification.id}, ${view.certification.created_at}; read ${view.read_at}.
${JSON.stringify(weakest)}`;
}

export function createTeachingReview({api, escape: e, toast, header, ask, showDialog, dialogHeader}) {
  const section = document.getElementById("teaching-review-section");
  section.innerHTML = '<div data-review-main></div><div class="regrade-host" data-regrade-host hidden></div>';
  const root = section.querySelector("[data-review-main]"), regradeHost = section.querySelector("[data-regrade-host]");
  const h = helpers({api, escape: e, showDialog, dialogHeader, toast});
  let route = "", catalog = null, view = null, pending = null, readAt = 0, generation = 0, comparison = null;
  let regrades = null, regradesFor = "", waiting = null, openedFor = "", countRead = 0;
  const current = () => location.hash.split("?")[0];
  const query = () => new URLSearchParams(location.hash.split("?")[1] || "");
  const delivery = () => route.startsWith("#grade-delivery");
  // Return's Regrade requests (REGRADES): #grade-delivery/<plan>?view=regrades[&request=<message id>].
  const regradesView = () => delivery() && Boolean(aid()) && query().get("view") === "regrades";
  const aid = () => route.split("/")[1] || "";
  const tab = () => delivery() ? "grade-delivery" : "teaching-review";
  function closeRegrades() {
    regrades?.destroy();
    regrades = null; regradesFor = "";
    regradeHost.replaceChildren();
    regradeHost.hidden = true;
  }
  // REGRADES: a closed page leaves Apply waiting. The shell retains this
  // section's DOM, so leaving the address must stop its continuation timer.
  window.addEventListener("hashchange", () => {
    if (regrades && (current() !== route || !regradesView() || regradesFor !== aid())) closeRegrades();
  });
  const labels = {changed_since: "changed since certification", complete: "complete", dr: "DR bookings", dropped_left_out: "dropped students left out",
    excused: "excused", flagged: "flagged responses", in_progress: "in progress", missing: "missing", not_on_gradescope: "students not covered on Gradescope",
    participants: "students", settled: "settled flags", zero_somewhere: "students with a zero", left_out: "left out", zero: "certified zeros",
    missing_evidence: "missing evidence", removed_or_unavailable_source: "removed or unavailable sources", unscored_question: "unscored questions",
    final: "final grades", sent: "sent", changed: "changed locally", not_sent: "not sent", accepted: "accepted", uncertain: "uncertain", failed: "failed"};
  function counts(values) { return Object.entries(values || {}).filter(([, count]) => typeof count === "number" && count > 0).map(([key, count]) => `${count} ${labels[key] || key.replaceAll("_", " ")}`).join(" · ") || "None recorded"; }
  // One date format with the other pages (a date and a minute, "Oct 5, 3:05 AM"; critique 2026-10-05), never seconds.
  const stamped = new Intl.DateTimeFormat(undefined, {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"});
  const when = stamp => stamp ? stamped.format(new Date(stamp)) : "";
  const grading = () => `#grading/${encodeURIComponent(aid())}?tab=export`;
  function insightMarkup() {
    if (!view.certification) return '<p>Certify this assignment’s grades to see its teaching evidence.</p>';
    return `<p>Certification from ${e(when(view.certification.created_at))}. These are historical certified grades, even if grading has changed since. Objective labels are the bank’s current classifications.</p>
      <div class="support-actions"><button class="button" type="button" data-review-draft="lesson" ${view.questions.length ? "" : "disabled"}>Draft review lesson</button><button class="button" type="button" data-review-draft="practice" ${view.questions.length ? "" : "disabled"}>Draft practice set</button><span class="field-hint">Opens a draft in Assistant using up to five questions with the lowest averages.</span></div>
      <h2>What should I revisit?</h2><p>A low average suggests a topic to review; it does not diagnose why students struggled. The sample excludes missing work, excused students and unavailable scans.</p>
      ${view.objectives.length ? `<ul>${view.objectives.map(o => `<li><strong>${e(o.title)}</strong>: ${o.earned_pct}% of points on average · ${o.observations} question responses across ${o.questions} question/version entries</li>`).join("")}</ul>` : '<p>No learning objectives are linked to the certified questions yet.</p>'}
      <p class="field-hint">${view.questions.filter(q => !q.objectives.length).length} untagged question/version entries. Excluded: ${e(counts(view.excluded))}. Exclusion counts are student records for zero, excused and left out; otherwise question responses.</p>
      <div class="support-list">${view.questions.map(q => `<article class="support-card" data-patch-key="${e(q.question_revision_id + q.rubric_id)}"><h3>${e(q.title)}${q.version ? ` · Version ${e(q.version)}` : ""}</h3>
        <p><strong>${q.earned_pct}%</strong> of points on average · ${q.n} responses · ${q.below_full} below full credit</p>
        <p>${q.objectives.map(o => e(o.title)).join(" · ") || "No learning objective tagged"}</p>
        <details><summary>Rubric evidence and source</summary><p class="support-words">${e(q.statement)}</p><ul>${q.rubric_lines.map(line => `<li>${line.selected}/${q.n} selected · ${e(String(line.points))} points · ${e(line.description)}</li>`).join("")}</ul>
        <p class="field-hint">Selections are frequencies, not diagnoses. The question and rubric shown are the revisions retained with this certification.</p></details>
        <a href="${grading()}&question=${encodeURIComponent(q.question_id)}">Open grading evidence</a></article>`).join("") || '<p>No present, scored FRQ evidence is available in this certification. Bubble-sheet item analysis remains in Certify.</p>'}</div>`;
  }
  function deliveryMarkup() {
    const gs = view.gradescope;
    const column = `#gradebook/${encodeURIComponent(view.term_id)}?assignment=${encodeURIComponent(view.assignment_id || '')}`;
    const cells = view.columns.reduce((n, c) => n + c.recorded_cells, 0);
    const published = view.publications.filter(p => p.status === 'complete');
    // ONE LINK PER PLACE (2026-10-18 review item 10): the three Canvas lines are all done in the gradebook, so only the first links it;
    // the two Gradescope lines open this page's own Gradescope details in place (`fold`), never Certify's page.
    const rows = [
      ['Certified', view.certification.state === 'current' ? `Current · ${when(view.certification.created_at)}` : view.certification.state === 'changed' ? 'Changed since certification' : 'Needs certification', grading(), 'Review and certify'],
      ['Recorded locally', view.columns.length ? `${cells} gradebook cells recorded` : 'No linked gradebook column', column, view.columns.length ? 'Open gradebook to sync, post or hide' : 'Choose a gradebook column'],
      ['Sent to Canvas', canvasSentText(view.columns)],
      ['Posted in Canvas', canvasPostedText(view.columns, when)],
      ['Sent to Gradescope', counts(gs.written.grades), `#grade-delivery/${encodeURIComponent(aid())}?open=gradescope`, 'Review sending grades', 'gradescope'],
      ['Published', published.length ? `${published.length} completed publication receipts · ${when([...published].sort((a,b) => String(b.created_at).localeCompare(String(a.created_at)))[0].created_at)}` : 'No completed publication receipt', `#grade-delivery/${encodeURIComponent(aid())}?open=gradescope`, 'Review publication', 'gradescope']
    ];
    const checklist = `<section class="delivery-checklist"><h2>Delivery checklist</h2><p>Each line is independent. Recording grades locally does not mean students can see them.</p><dl>${rows.map(([title, detail, href, action, fold]) => `<div><dt>${e(title)}</dt><dd>${e(detail)}${href ? ` <a class="link-arrow" href="${e(href)}"${fold ? ` data-delivery-open="${e(fold)}"` : ''}>${e(action)}</a>` : ''}</dd></div>`).join('')}</dl></section>`;
    // Plain words (ASSIGNMENT-WORKSPACE "ONE NAVIGATION", 2026-10-05): these are Faculty's own records of what it sent.
    return `<p>Faculty's records of what it sent, read ${e(when(view.read_at))}. To see what students see now, check Gradescope or compare with Canvas.</p>${checklist}
      <div class="support-list"><details class="disclosure" data-delivery-details="certification"><summary>Grading and certification details</summary><p>Certification: <strong>${e(({current: "Current", changed: "Changed since certification", none: "Not certified"})[view.certification.state] || "Not certified")}</strong></p><p>${e(counts(view.counts))}</p><a class="link-arrow" href="${grading()}">Review or certify grades</a></details>
      <details class="disclosure" data-delivery-details="canvas"><summary>Gradebook and Canvas details</summary>${view.columns.length ? view.columns.map(c => `<h3>${e(c.title)}</h3><p>${c.recorded_cells} gradebook cells · ${c.without_receipt} cells without a send receipt</p><p>Destination receipts: ${e(counts(c.receipts))}</p><p>Posted in Canvas: ${e(canvasPostedText([c], when))}</p><p class="field-hint">${c.last_receipt ? `Latest receipt ${e(when(c.last_receipt))}.` : "No send recorded."} “Changed” means the local cell now differs from the accepted send. A receipt can belong to an earlier destination mapping.</p><a href="#gradebook/${encodeURIComponent(view.term_id)}?item=${encodeURIComponent(c.id)}">Open gradebook column</a>`).join("") : '<p>No current gradebook column is linked to this assignment.</p>'}
      <div class="support-actions">${view.columns.length ? '<button class="button" type="button" data-review-compare="canvas">Compare with Canvas now</button>' : `<a class="button" href="${e(column)}">Choose a gradebook column</a>`}</div></details>
      <details class="disclosure" data-delivery-details="gradescope"><summary>Gradescope details</summary><p>Written grade receipts: ${e(counts(gs.written.grades))}</p><p>Papers: ${gs.written.papers.sent}/${gs.written.papers.total} covered by receipts.</p>${gs.sheets ? `<p>Bubble sheets: ${e(counts(gs.sheets))}</p>` : ""}
      <p>Publication history: ${view.publications.length ? view.publications.map(p => `${e(p.status)} (${e(when(p.created_at))})`).join("; ") : "No publication receipt recorded"}.</p>
      <div class="support-actions"><button class="button" type="button" data-review-compare="gradescope">Check Gradescope now</button><a class="link-arrow" href="${grading()}&open=gradescope">Send or publish on Gradescope</a></div></details></div>
      <section aria-live="polite" data-review-comparison>${comparisonMarkup()}</section>`;
  }
  function comparisonMarkup() {
    if (!comparison) return "";
    if (comparison.busy) return '<p role="status">Reading the destination…</p>';
    if (comparison.error) return `<p role="alert">${e(comparison.error)}</p>`;
    const data = comparison.data;
    if (comparison.kind === "canvas") {
      const ids = new Set(view.columns.map(c => c.id));
      const columns = (data.columns || []).filter(c => ids.has(c.item_id));
      return `<h2>Canvas comparison · ${e(comparison.at)}</h2><p>Nothing was sent. Sync with Canvas checks everything again.</p>${columns.length ? columns.map(c => `<article class="support-card"><h3>${e(c.title)} · ${e(c.course)}</h3><p>${e(counts(c.counts))}</p>${(c.problems || []).map(p => `<p>${e(p)}</p>`).join("")}</article>`).join("") : '<p>No comparable destination for these columns. Open the gradebook to check Canvas links.</p>'}`;
    }
    return `<h2>Gradescope comparison · ${e(comparison.at)}</h2><p>Nothing was published.</p>${(data.problems || []).map(p => `<p>${e(p)}</p>`).join("")}
      <ul>${(data.rows || []).map(r => `<li>${e(r.course || r.course_title || "Course")} · ${e(r.title || r.target_title || "Assignment")}: ${e(r.problem || (r.published === true ? "Published" : r.published === false ? "Not published" : r.status || "See publication controls for details"))}</li>`).join("")}</ul><a href="${grading()}&open=gradescope">Open publication details</a>`;
  }
  function draw() {
    if (current() !== route) return;
    const asks = regradesView();
    const title = asks ? "Regrade requests" : delivery() ? "Grade delivery" : "Teaching review";
    header?.set({route: location.hash, title, subtitle: view?.title || "Choose an assignment.", action: null});
    const plan = aid() ? `/${encodeURIComponent(aid())}` : "";
    // The open count is the needs-you block (DESIGN-LANGUAGE §1.2): requests waiting for the instructor's answer.
    const count = waiting ? ` <b>${e(waiting)}</b>` : "";
    patchChildren(root, `<div class="support-page"><nav class="support-tabs" aria-label="Return views"><a class="button" href="#teaching-review${plan}" ${!delivery() ? 'aria-current="page"' : ""}>Teaching review</a><a class="button" href="#grade-delivery${plan}" ${delivery() && !asks ? 'aria-current="page"' : ""}>Grade delivery</a>${aid() ? `<a class="button" href="#grade-delivery${plan}?view=regrades" data-review-regrades ${asks ? 'aria-current="page"' : ""}${waiting ? " data-needs" : ""}>Regrade requests${count}</a>` : ""}</nav>
      <div class="support-toolbar"><label class="field">Assignment<select data-review-assessment><option value="">Choose an assignment</option>${(catalog?.assessments || []).map(a => `<option value="${e(a.id)}" ${a.id === aid() ? "selected" : ""}>${e(`${a.code} · ${a.term === "Other" ? a.term_other : a.term} ${a.year} · ${a.title}${a.is_current ? "" : " (past term)"}`)}</option>`).join("")}</select></label>${asks ? "" : '<button class="button" type="button" data-review-refresh>Check again</button>'}</div>
      ${asks ? "" : view ? delivery() ? deliveryMarkup() : insightMarkup() : '<p>Choose an assignment to begin. Only Faculty records are read until you ask for a destination comparison.</p>'}</div>`);
    // `#grade-delivery/<plan>?open=gradescope` opens the Gradescope details once per address; a later redraw leaves the fold as you left it.
    if (view && delivery() && query().get("open") === "gradescope" && openedFor !== location.hash) { openedFor = location.hash; openFold("gradescope", false); }
    syncRegrades();
  }
  // Return's own Gradescope details, opened in place (item 10): the tab stays Return and the address does not change.
  function openFold(name, focus = true) {
    const fold = root.querySelector(`[data-delivery-details="${CSS.escape(name)}"]`);
    if (!fold) return;
    fold.open = true;
    fold.scrollIntoView({block: "start"});
    if (focus) fold.querySelector("summary")?.focus({preventScroll: true});
  }
  // The panel is mounted once per assessment and kept while the address only changes its request.
  function syncRegrades() {
    const asks = regradesView();
    regradeHost.hidden = !asks;
    if (!asks) { closeRegrades(); return; }
    const request = query().get("request") || "";
    if (regrades && regradesFor === aid()) { void regrades.route(request).catch(cause => toast(cause.message, true)); return; }
    regrades?.destroy();
    regradesFor = aid();
    regrades = mountRegradePanel(regradeHost, aid(), h, {request,
      go: id => { location.hash = `#grade-delivery/${encodeURIComponent(aid())}?${new URLSearchParams({view: "regrades", request: id})}`; },
      onChange: () => void counted(true)});
    void regrades.start().catch(cause => toast(cause.message, true));
  }
  // The sub-tab's count: the plan's open requests (the Review step's anonymous list, no roster read).
  async function counted(redraw = false) {
    const id = aid(), mine = generation, read = ++countRead;
    const currentCount = () => id === aid() && mine === generation && read === countRead && current() === route;
    if (!id) { waiting = null; return; }
    try {
      const found = await api(`/api/grading/assignments/${encodeURIComponent(id)}/regrade-requests`);
      if (!currentCount()) return;
      waiting = found.counts?.open || 0;
    } catch { if (!currentCount()) return; waiting = null; }
    if (redraw) draw();
  }
  async function render(force = false) {
    const next = current();
    if (!/^#(teaching-review|grade-delivery)(\/|$)/.test(next)) return;
    if (next !== route) { route = next; view = null; comparison = null; generation++; pending = null; readAt = 0; waiting = null; }
    if (!pending && !force && readAt && Date.now() - readAt < 60000) { draw(); return; }  // the address's query may have changed
    if (pending) return pending;
    const mine = generation;
    pending = (async () => {
      try {
        const [list, detail] = await Promise.all([api('/api/teaching-review'), aid() ? api(`/api/teaching-review/${encodeURIComponent(aid())}${delivery() ? "/delivery" : ""}`) : null, counted()]);
        if (mine !== generation || current() !== route) return;
        catalog = list; view = detail; readAt = Date.now(); comparison = null; draw();
      } catch (cause) { if (mine === generation && current() === route) { toast(cause.message, true); draw(); } }
      finally { if (mine === generation) pending = null; }
    })();
    return pending;
  }
  root.addEventListener('change', event => { if (event.target.matches('[data-review-assessment]')) location.hash = `#${tab()}${event.target.value ? `/${event.target.value}` : ""}`; });
  root.addEventListener('click', async event => {
    const opens = event.target.closest('[data-delivery-open]');
    if (opens && !event.ctrlKey && !event.metaKey && !event.shiftKey && event.button === 0) { event.preventDefault(); openFold(opens.dataset.deliveryOpen); return; }
    const button = event.target.closest('button');
    if (!button) return;
    if (button.hasAttribute('data-review-refresh')) { await render(true); return; }
    if (button.dataset.reviewDraft && view) {
      button.disabled = true;
      try { await ask(draftRequest(view, button.dataset.reviewDraft)); } catch (cause) { toast(cause.message, true); }
      finally { button.disabled = false; }
    }
    if (button.dataset.reviewCompare && view && !comparison?.busy) {
      const mine = generation, kind = button.dataset.reviewCompare;
      comparison = {busy: true}; draw();
      try {
        const data = await api(kind === 'canvas' ? `/api/gradebook/terms/${view.term_id}/canvas-sync/preview` : `/api/grading/gradescope/assignments/${aid()}/publish/preview`, 'POST', {});
        if (mine === generation) comparison = {kind, data, at: new Date().toLocaleString()};
      } catch (cause) { if (mine === generation) comparison = {error: cause.message}; }
      finally { if (mine === generation) draw(); }
    }
  });
  return {render};
}
