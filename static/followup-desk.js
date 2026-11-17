// TEACHING-WORKFLOWS ACTION DESK: projections only; actions and commitments retain their existing owners.
import { proposalLabel, resultConfirmed } from "./followup-suggestions.js";
import { wallDate } from "./ui.js";

export const commitmentOpen = c => (c.effective_state || c.state || "open") === "open";
export const openCommitments = item => [...item?.commitments || [], ...(item?.related_commitments || []).flatMap(r => r.commitments || [])].filter(commitmentOpen);

/** A receipt is completion; an HTTP response, proposal, decline or uncertain send is not. */
export function actionFinished(action) {
  if (action.replaced) return action.replaced.status === "accepted";
  if (action.status !== "succeeded") return false;
  // The server's outcome decides first, as `finishedAs` does (assistant/outcomes.py).
  if (action.outcome) return action.outcome === "succeeded";
  return resultConfirmed(action);
}

/** ACTION DESK (requirement, 2026-11-06): a succeeded card whose outcome says more is open or failed, not completed: partial,
 * failed or uncertain needs a look ("failed"), started is still running ("open"); cancelled is decided and not listed. */
const unfinishedOutcome = action => action.replaced || action.status !== "succeeded" || !action.outcome || ["succeeded", "cancelled"].includes(action.outcome) ? ""
  : action.outcome === "started" ? "open" : "failed";

export function deskTasks(item) {
  const s = item.suggestions || {}, rows = [];
  const actions = [...s.actions || [], ...s.chat || []];
  const linked = new Set((item.commitments || []).map(c => c.action_id).filter(Boolean));
  const ranks = a => a.body?.commitments?.length && !["waiting","resolved"].includes(a.body?.state) ? 0 : ["student_support.create", "student_support.revise"].includes(a.endpoint) && a.body?.kind !== "appointment" && !a.body?.end_time ? 2 : a.endpoint === "gmail_mail.send" ? 1 : 0;
  const ordered = actions.filter(a => !a.withdrawn && (s.state !== "superseded" && (a.pending || ["approved", "approving", "failed"].includes(a.status)) || actionFinished(a) || unfinishedOutcome(a)))
    .sort((a, b) => ranks(a) - ranks(b));
  for (const [index, a] of ordered.entries()) {
    if (linked.has(a.id)) continue;
    const done = actionFinished(a), earlier = ordered.slice(0, index).filter(p => ranks(p) < ranks(a) && !actionFinished(p));
    const replyBlocks = a.endpoint === "gmail_mail.send" && openCommitments(item).some(c => c.blocks_reply);
    rows.push({id: a.id, title: proposalLabel(a), kind: "action", action: a, state: done ? "completed" : unfinishedOutcome(a) || (a.status === "failed" ? "failed" : "open"),
      waiting_on: "you", blocked: !done && (earlier.length > 0 || replyBlocks), reason: replyBlocks ? "Finish the reply prerequisites first" : earlier.length ? "After the preceding work is confirmed" : unfinishedOutcome(a) ? unfinishedOutcome(a) === "open" ? "Running…" : "Partly done / Needs a look" : ["approved", "approving"].includes(a.status) ? "Running…" : done ? "Confirmed action receipt" : "Ready for your review"});
  }
  for (const c of item.commitments || []) {
    const blocked = c.depends_on?.some(id => {const p = (item.commitments || []).find(p => p.id === id);return (p?.effective_state || p?.state) !== "completed";});
    rows.push({...c, kind: "commitment", commitment_kind: c.kind, state: c.effective_state || c.state, blocked,
      reason: blocked ? "Finish its prerequisites first" : c.completion_note || (c.check_back ? `Check back ${wallDate(c.check_back)}` : c.waiting_on === "student" ? "Waiting on the student" : "No check-back date set")});
  }
  for (const w of s.watches || []) {
    if (w.state === "removed") continue;
    const linked = w.commitment_id && rows.find(t => t.kind === "commitment" && t.id === w.commitment_id);
    if (linked) {linked.watch = w;continue;}
    rows.push({id: w.id, title: w.title, kind: "watch", state: w.state === "triggered" ? w.detail?.run_id || w.detail?.completed ? "completed" : "failed" : w.state === "error" ? "failed" : "open",
      waiting_on: "date", check_back: w.check_back, reason: w.detail?.message || (w.check_back ? `Check back ${wallDate(w.check_back)}` : "When the watched record changes"), watch: w});
  }
  for (const owner of item.related_commitments || []) {
    for (const c of owner.commitments || []) rows.push({...c, id:`${owner.owner_id}:${c.id}`, kind:"related", state:c.effective_state || c.state,
      source_key:owner.source_key, reason:`From joined follow-up: ${owner.title}. Kept with its original record.`});
  }
  if (!rows.some(r => r.state === "open" || r.state === "failed") && !item.commitments?.length && item.next && !["resolved", "fyi"].includes(item.state)) {
    rows.push({id: "next", title: item.next, kind: "unstructured", state: "open", waiting_on: item.state === "waiting" ? "student" : item.state === "snoozed" ? "date" : "you",
      check_back: item.date, reason: "Recorded next step · split it into tasks to track each separately"});
  }
  const rank = t => t.kind === "action" ? ranks(t.action) : t.kind === "watch" || t.commitment_kind === "check_back" ? 3 : t.commitment_kind === "reply" ? 1 : 0;
  const sorted = [], added = new Set();
  const put = t => {if(added.has(t.id))return;added.add(t.id);for(const id of t.depends_on || []) {const p=rows.find(r=>r.id===id);if(p)put(p);}sorted.push(t);};
  rows.sort((a,b)=>rank(a)-rank(b)).forEach(put);
  return sorted;
}

export function deskTitle(item) {
  const tasks = deskTasks(item), next = tasks.find(t => t.state === "open" && t.waiting_on === "you" && !t.blocked)
    || tasks.find(t => t.state === "open") || tasks[0];
  return next?.title || item.title;
}

export function nextDeskAction(item) {
  const tasks = deskTasks(item), first = tasks.find(t => ["open", "failed"].includes(t.state) && t.waiting_on === "you" && !t.blocked);
  if (item.suggestions?.question) return {title: "Answer the Assistant’s question", detail: "Your answer updates the unapproved suggestions.", target: "question"};
  if (first) return {title: first.title, detail: first.reason, target: first.kind === "action" ? first.id : "tasks"};
  if (item.commitments?.length && !openCommitments(item).length && !["needs_reply", "resolved"].includes(item.state))
    return {title:"All recorded tasks are complete", detail:"Resolve this follow-up if no further reply is needed.", target:"resolve"};
  if (openCommitments(item).some(c => c.waiting_on === "date") || tasks.some(t => t.kind === "watch" && t.state === "open"))
    return {title: "A check-back is still scheduled", detail: "The reply and future work are tracked separately.", target: "tasks"};
  if (item.state === "waiting") return {title: item.student?.coordinator ? "Waiting on the coordinator" : "Waiting on the student", detail: item.next || "A reply to your last message", target: "conversation"};
  return {title: item.next || (item.attention ? "Review this conversation" : "No action waits on you"), detail: "", target: "conversation"};
}

/** RECORDINGS "Student meetings": a task heard in the student's own recorded meeting links the moment it was said, or "". */
export function heardLink(task, e) {
  const href = String(task?.heard?.href || "");
  return /^#recordings\/[A-Za-z0-9-]{1,64}\?t=\d{1,12}$/.test(href) ? ` · <a class="link-arrow" href="${e(href)}">Said in your meeting</a>` : "";
}

export function deskPlanMarkup(item, index, e) {
  const tasks = deskTasks(item), open = tasks.filter(t => ["open", "failed"].includes(t.state));
  const row = t => `<li data-patch-key="desk-task-${e(t.id)}" data-task-state="${e(t.state)}"><div><strong>${e(t.title)}</strong>
    <p class="field-hint">${e(t.reason || "")}${t.blocks_reply ? " · Reply prerequisite" : ""}${heardLink(t, e)}</p></div><div class="support-task-controls">${t.kind === "action"
      ? `<button class="text-button" type="button" data-desk-action="${e(t.id)}">${t.state === "completed" ? "Receipt" : "Review"}</button>`
      : t.kind === "commitment" ? `<button class="text-button" type="button" data-commitment-edit="${e(t.id)}">${t.state === "completed" ? "Details" : "Edit"}</button>${t.state === "open" && !t.blocked && !t.action_id ? `<button class="text-button" type="button" data-commitment-complete="${e(t.id)}">Mark completed</button>` : ""}${t.state === "open" ? t.watch ? '<button class="text-button" type="button" data-desk-watch>View watch</button>' : `<button class="text-button" type="button" data-commitment-watch="${e(t.id)}">Watch…</button>` : ""}`
      : t.kind === "watch" ? '<button class="text-button" type="button" data-desk-watch>View watch</button>'
      : t.kind === "related" ? `<button class="text-button" type="button" data-support-separate="${index}" data-key="${e(t.source_key)}">Keep separate to edit</button>`
      : item.suggestions?.unavailable ? '<button class="text-button" type="button" data-commitment-add>Add separate tasks</button>'
      : '<button class="text-button" type="button" data-desk-split>Prepare separate tasks</button>'}</div></li>`;
  return `<section class="support-task-plan" data-patch-key="task-plan" aria-label="Separate tasks"><div class="support-task-heading"><h4>Next steps</h4>
    <button class="text-button" type="button" data-commitment-add>Add task</button></div>${open.length ? `<ol class="support-task-list">${open.map(row).join("")}</ol>`
      : '<p class="field-hint">No separate tasks remain open.</p>'}${tasks.some(t => ["completed", "cancelled"].includes(t.state)) ? `<details class="disclosure support-task-history"><summary>Completed and cancelled tasks</summary><ol class="support-task-list">${tasks.filter(t => ["completed", "cancelled"].includes(t.state)).map(row).join("")}</ol></details>` : ""}</section>`;
}

export function commitmentsMarkup(shown, e) {
  const rows = shown.flatMap(({item, index}) => deskTasks(item).filter(t => ["open", "failed"].includes(t.state)).map(task => ({item, index, task})));
  return `<section class="support-commitments" data-patch-key="commitments-overview"><div class="support-task-heading"><h3>What still needs to happen</h3>
    <span class="field-hint">${rows.length} tasks · ${new Set(rows.map(r => r.index)).size} conversations</span></div>${rows.length
      ? `<div class="data-table" role="region" aria-label="Open tasks"><table><thead><tr><th>Next step</th><th>Student</th><th>Check back</th><th>Waiting on</th><th>State</th></tr></thead><tbody>${rows.map(({item, index, task:t}) => `<tr data-patch-key="commitment-${index}-${e(t.id)}"><td><button class="text-button" type="button" data-desk-open="${index}">${e(t.title)}</button></td><td>${e(item.student?.name || "Student")}<br><span class="field-hint">${e(item.student?.code || "")}</span></td><td>${e(t.check_back ? wallDate(t.check_back) : "No date set")}</td><td>${e(({you:"You",student:"Student",date:"Date or record change"})[t.waiting_on] || "You")}</td><td>${e(t.blocked ? "Prerequisite unfinished" : t.state === "failed" ? "Needs attention" : t.kind === "action" ? "Review ready" : "Open")}</td></tr>`).join("")}</tbody></table></div>`
      : '<p class="field-hint">No open tasks in this view.</p>'}</section>`;
}
