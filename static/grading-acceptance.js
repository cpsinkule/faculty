// Trial acceptance and the gate. The instructor's confirm/overturn review of what the policy accepted IS the benchmark report;
// real automatic acceptance can only be switched on from a report whose upper confidence bound is within the limit.
// Pure presentation of /acceptance: nothing here accepts, approves or changes a rubric line.
import {COMPUTE} from "./grading-jobs.js";
const percent = value => value === null || value === undefined ? "–" : `${(100 * value).toFixed(value < .1 ? 1 : 0)}%`;

export function gateSummary(report) {
  const gate = report.blind;
  if (!gate.reviewed) return "No blind reviews yet. Review the blind responses first: you grade them before the AI’s decision is shown.";
  const bound = `${gate.overturned} overturned in ${gate.reviewed} blind reviews: observed ${percent(gate.error_rate)}, but the true rate could be as high as ${percent(gate.upper_bound_95)} (upper 95% bound).`;
  if (gate.eligible) return `${bound} That is within your limit of ${percent(gate.limit)}.`;
  return `${bound} Your limit is ${percent(gate.limit)}. ${gate.more_reviews_needed === null ? "With these errors the limit is out of reach; fix what the overturned responses show first." : `About ${gate.more_reviews_needed} more error-free blind reviews would reach it.`}`;
}

export async function acceptanceDialog(plan, questionId, h, refresh, active, openQueue) {
  if (!questionId) { h.toast("Choose a question first: acceptance is measured per question and exam version.", true); return; }
  const [data, queue] = await Promise.all([h.api(`/api/grading/assignments/${plan.id}/acceptance?question_id=${encodeURIComponent(questionId)}`),
    h.api(`/api/grading/assignments/${plan.id}/acceptance/queue?question_id=${encodeURIComponent(questionId)}`)]);
  if (!active()) return;
  const question = plan.questions.find(q => q.question_id === questionId);
  const body = data.reports.length ? data.reports.map((report, n) => {
    const status = report.permission.status, roles = Object.entries(report.scope.roles || {}).map(([role, r]) => `${role}: ${r.endpoint || r.model}${r.lineage ? ` (${r.lineage})` : ""}`).join(" · ");
    return `<section class="grading-panel" data-acceptance-scope="${h.e(report.scope_sha256)}"><h3>${h.e(COMPUTE[report.scope.tier] || report.scope.tier || "This grading method")} · ${h.e(report.accepted)} accepted${report.automatic ? ` (${h.e(report.automatic)} automatically)` : ""}</h3><p class="supporting-copy">${h.e(roles)} · rubric, question revision, prompts, models and risk rules are part of this scope; changing any of them starts a new one.</p>
      <p><strong>${h.e(gateSummary(report))}</strong></p><p>${h.e(report.awaiting_review)} still await your look · with the proposal visible: ${h.e(report.seen.overturned)} overturned in ${h.e(report.seen.reviewed)} · ${h.e(report.blind.students)} students in the blind sample. ${h.e(report.note)}</p>
      ${report.overturned.length ? `<details open><summary>Overturned responses, with the signal that should have caught each</summary><ul>${report.overturned.map(o => `<li>${o.blind ? "Blind" : "Seen"} · ${h.e(o.kind)} · <a href="#grading/${h.e(plan.id)}?tab=review&response=${h.e(o.response_id)}">open</a><ul>${o.should_have_caught.map(line => `<li>${h.e(line)}</li>`).join("")}</ul></li>`).join("")}</ul></details>` : ""}
      <p>Automatic acceptance: <strong>${h.e({none: "off (the default)", enabled: "ON for this exact scope", suspended: "suspended", revoked: "revoked"}[status] || status)}</strong>${report.permission.events?.length ? ` · ${h.e(report.permission.events.at(-1).reason || "")}` : ""}</p>
      <div class="grading-actions">${status === "enabled" ? h.button("revoke-acceptance", "Turn automatic acceptance off", `data-scope="${h.e(report.scope_sha256)}"`) : h.button("enable-acceptance", "Turn on automatic acceptance for this scope", `data-scope="${h.e(report.scope_sha256)}" data-index="${n}"`)}</div></section>`;
  }).join("") : '<p class="empty-state">Nothing has been accepted for this question yet. With trial acceptance on, the risk policy accepts what passes every rule; those scores are never final and never exported until you have looked.</p>';
  const form = h.dialog(`Trial acceptance · ${question ? question.content.title : ""}`, "Trial-accepted scores are a test of the policy: never final, never exported. Your confirm or overturn of each one is the measurement.",
    `<p>${h.e(queue.waiting.length)} accepted response${queue.waiting.length === 1 ? "" : "s"} await your look${queue.blind ? ` · ${h.e(queue.blind)} blind (grade them before the AI’s decision is shown)` : ""}${queue.audit ? ` · ${h.e(queue.audit)} audit sample` : ""}.</p><div class="grading-actions">${queue.waiting.length ? h.button("open-queue", queue.blind ? "Review the blind responses first" : "Review accepted responses") : ""}</div>${body}`,
    "Done", async () => { h.close(); });
  form.querySelector('[data-grading-action="open-queue"]')?.addEventListener("click", () => { h.close(); openQueue(queue.waiting.map(row => row.response_id)); });
  form.querySelectorAll('[data-grading-action="enable-acceptance"]').forEach(button => button.onclick = () => {
    const report = data.reports[Number(button.dataset.index)];
    h.dialog("Let scores become final without your look?", gateSummary(report), `<p>This applies only to this exact scope. One response in twenty still comes to you as an audit, and a single overturned one suspends it at once.</p>${h.field("Error limit (upper 95% bound)", "limit", report.blind.limit, "number", 'min="0.0001" max="0.5" step="any"')}${h.field("Audit share", "audit", .05, "number", 'min="0.01" max="1" step="0.01"')}${h.check("Scores of this question may become final without my look, within this report’s scope", "confirmed")}`, "Turn on automatic acceptance", async (values, _, live) => {
      if (!values.has("confirmed")) throw new Error("Confirm it explicitly, or close this window.");
      await h.api(`/api/grading/assignments/${plan.id}/questions/${questionId}/acceptance/enable`, "POST", {scope_sha256: report.scope_sha256, error_limit: Number(values.get("limit")), audit_share: Number(values.get("audit")), confirm_automatic_acceptance: true});
      if (live()) { h.close(); await refresh(); }
    });
  });
  form.querySelectorAll('[data-grading-action="revoke-acceptance"]').forEach(button => button.onclick = () => h.run(button, async () => {
    await h.api(`/api/grading/assignments/${plan.id}/questions/${questionId}/acceptance/disable`, "POST", {scope_sha256: button.dataset.scope});
    h.close(); await refresh();
  }));
}
