import {reviewReasonMarkup} from "./grading-model-comments.js";
import {rubricDisplay} from "./grading-rubric-display.js";
import {mathText, renderMath} from "./grading-math.js";
import {cropButton} from "./grading-crops.js";
// The answer key for every question at once, and review by answer group.
// A matching answer is evidence only: nothing here approves work the instructor has not looked at.
import {inPrintedOrder, versionTitle} from "./grading-guide.js";

// One part per line: "a: 3x2 | 3 x 2". A single answer needs no label: "x=6 | 6".
export function keyText(key) {
  return key.map(part => `${part.label ? `${part.label}: ` : ""}${part.accept.join(" | ")}`).join("\n");
}
// The accepted answers of one line: split at each "|" that stands BETWEEN answers, never at one inside braces, brackets or
// parentheses (the column bar of \begin{array}{ccc|c}, an absolute value written (|x|)), after a backslash (\|) or in \left| … \right|.
// (2026-09-27: every "|" used to split, and a matrix key was saved as fragments no answer could match.)
export function splitAnswers(text) {
  const parts = [];
  let depth = 0, start = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === "\\") {
      const sized = text.slice(i).match(/^\\(left|right)(?![A-Za-z])\s*(\\[A-Za-z]+|\\.|.)/);
      if (sized) { depth = Math.max(0, depth + (sized[1] === "left" ? 1 : -1)); i += sized[0].length - 1; }
      else i += 1;  // an escaped character (\|, \{) or the first letter of a command: never a separator
      continue;
    }
    if ("{[(".includes(c)) depth += 1;
    else if ("}])".includes(c)) depth = Math.max(0, depth - 1);
    else if (c === "|" && depth === 0) { parts.push(text.slice(start, i)); start = i + 1; }
  }
  parts.push(text.slice(start));
  return parts.map(answer => answer.trim()).filter(Boolean);
}
export function parseKey(value) {
  return value.split("\n").map(line => line.trim()).filter(Boolean).map(line => {
    const labelled = line.match(/^([A-Za-z][^:=|]{0,11}):\s*(.+)$/);
    return {label: labelled ? labelled[1].trim() : "", accept: splitAnswers(labelled ? labelled[2] : line)};
  });
}
// An answer key's parts as a page shows them, "Part a:" for a lettered part and "x:" for a labelled value, each accepted answer
// typeset: a key is written as bare LaTeX, without $ signs (2026-09-25: \dfrac and matrices showed as their source).
export const keyMarkup = (key, h) => key.map(part => `<p>${part.label ? `<strong>${/^[a-h]$/.test(part.label) ? `Part ${h.e(part.label)}` : h.e(part.label)}:</strong> ` : ""}${part.accept.map(value => mathText(value, h, true)).join(' <span class="supporting-copy">or</span> ')}</p>`).join("");
// The key a question shows before one is saved: read from its rubric, else read from the blank exam under its current rubric.
export const shownKey = q => q.answer.key.length ? q.answer.key : q.answer.proposed_key.length ? q.answer.proposed_key
  : q.answer.read_key?.current && q.answer.read_key.status === "complete" ? q.answer.read_key.key : [];

const CHECKS = {match: "Matches your key", mismatch: "Does not match your key", unknown: "Not checked against a key"};
const WORK = {shown: "Work shown", thin: "Little work", none: "No work visible", unknown: "Work not measured"};

export function answerKeyDialog(plan, h, refresh, questionId = null) {
  const questions = inPrintedOrder(plan.questions).filter(q => q.rubric && (!questionId || q.question_id === questionId));
  if (!questions.length) { h.toast("Upload the rubrics first; the answer key is read from them for you to check.", true); return; }
  const dialog = document.querySelector("#dialog");
  const form = h.dialog("Answer key and solutions", "The key only sorts and cross-checks responses. It never awards credit: the rubric pays for the work, and you approve every group yourself.",
    `<p class="supporting-copy">One part per line, alternatives separated by <code>|</code>. Label parts when there are several: <code>a: 3x2 | 3 x 2</code>. Where a key was read from the rubric’s full-credit line, check it before saving.</p>${questions.map((q, n) => {
      const proposed = !q.answer.key.length && shownKey(q).length ? (q.answer.proposed_key.length ? "the rubric" : "the blank assignment") : "";
      const roles = q.answer.roles;
      return `<fieldset class="grading-key"><legend>${h.e(versionTitle(q))} · ${h.e(q.content.max_points)} points</legend>${proposed ? `<p class="grading-chip grading-chip-todo">Read from ${proposed} — check it</p>` : ""}${h.area("Accepted final answers", `key-${n}`, keyText(shownKey(q)))}<div class="grading-key-preview" data-key-preview="${n}" aria-live="polite"></div><small>${roles?.work_required ? `This rubric pays for the work${roles.no_work ? ` and has its own line for a correct answer without it (${h.e(rubricDisplay(q.rubric.rules).label(roles.no_work))})` : ""}: a matching answer with little work is set apart for you, never given full credit.` : "This rubric has no line about missing work. Matching answers are still split by how much work is visible."}</small><details><summary>Worked solution and problem text</summary>${h.area("Worked solution (shown to the grader)", `solution-${n}`, q.content.worked_solution || "")}${h.area("Problem text (read from the blank assignment; edit if needed)", `problem-${n}`, q.content.problem || "")}</details></fieldset>`;
    }).join("")}`, "Save answer key", async (values, _, live) => {
      const entries = questions.map((q, n) => ({question_id: q.question_id, version: q.version, answer_key: parseKey(values.get(`key-${n}`)),
        worked_solution: values.get(`solution-${n}`), problem: values.get(`problem-${n}`)}));
      await h.api(`/api/grading/assignments/${plan.id}/answer-key`, "POST", {version: plan.version, questions: entries});
      if (live()) { h.close(); await refresh(); h.toast("Answer key saved. Rubric approvals and confirmed locations are unchanged."); }
    });
  dialog.classList.add("dialog-wide");
  dialog.addEventListener("close", () => dialog.classList.remove("dialog-wide"), {once: true});
  // Each key typeset beneath its box as it is typed, exactly as the page will show it once saved.
  questions.forEach((_, n) => {
    const area = form.elements.namedItem(`key-${n}`), preview = form.querySelector(`[data-key-preview="${n}"]`);
    const draw = () => { preview.innerHTML = keyMarkup(parseKey(area.value), h); renderMath(preview); };
    area.addEventListener("input", draw);
    draw();
  });
}

export async function answerGroupsDialog(plan, questionId, h, refresh, active, afterApproval = false) {
  const listing = await h.api(`/api/grading/assignments/${plan.id}/answer-groups?question_id=${encodeURIComponent(questionId)}`);
  if (!active()) return;
  const question = plan.questions.find(q => q.question_id === questionId);
  const display = rubricDisplay({items: listing.rubric});
  if (!listing.groups.length) {
    h.toast(afterApproval ? "Every group of this question is reviewed." : "No current, unreviewed proposals for this question.");
    if (afterApproval) await refresh();
    return;
  }
  const dialog = document.querySelector("#dialog");
  const wide = () => { dialog.classList.add("dialog-wide"); dialog.addEventListener("close", () => dialog.classList.remove("dialog-wide"), {once: true}); };
  const summary = g => `${g.answers.length ? g.answers.map(a => h.e(a)).join(" · ") : "No final answer read"}`;
  const chips = g => `<span class="grading-chip grading-chip-${g.check === "match" ? "done" : g.check === "mismatch" ? "attention" : "waiting"}">${h.e(CHECKS[g.check])}</span><span class="grading-chip grading-chip-${g.work === "shown" ? "done" : "todo"}">${h.e(WORK[g.work])}</span>${g.check === "match" && g.readers_agree === false ? '<span class="grading-chip grading-chip-attention">Only one reading shows this answer</span>' : ""}${g.flagged ? `<span class="grading-chip grading-chip-attention">${h.e(g.flagged)} flagged for you</span>` : ""}${g.accepted === "trial" ? '<span class="grading-chip grading-chip-todo">Trial-accepted · your look is the test</span>' : ""}${g.independent_votes > 1 ? `<span class="grading-chip grading-chip-done">${h.e(g.independent_votes)} recorded votes</span>` : ""}`;
  function list() {
    const form = h.dialog(`${versionTitle(question)}: ${listing.responses} responses in ${listing.groups.length} groups`, listing.note,
      `<ol class="grading-group-list">${listing.groups.map((g, n) => `<li><div><strong>${summary(g)}</strong><p class="grading-chips">${chips(g)}</p><small>Proposed: ${display.ordered(g.applied_items).map(i => `${h.e(display.label(i.id))} (${h.e(i.points)} points)`).join(", ") || "no rubric line"} · ${g.score?.score ?? "no"} points</small></div><button type="button" class="button ${n === 0 ? "primary" : ""}" data-open-group="${h.e(g.id)}">Look at ${h.e(g.size)} response${g.size === 1 ? "" : "s"}</button></li>`).join("")}</ol>`,
      "Done", async (_, __, live) => { if (live()) { h.close(); await refresh(); } });
    wide();
    form.querySelectorAll("[data-open-group]").forEach(button => button.onclick = () => open(listing.groups.find(g => g.id === button.dataset.openGroup)));
  }
  function open(group) {
    const form = h.dialog(`${summary(group)} · ${group.size} response${group.size === 1 ? "" : "s"}`, "Look at every response. Untick any that does not belong; it stays for individual review.",
      `<p class="grading-chips">${chips(group)}</p>${group.work_required && group.work !== "shown" ? '<p class="grading-warning">This rubric pays for the work. These responses show little or none, so check them against its no-work and step lines rather than full credit.</p>' : ""}
      <fieldset class="grading-key"><legend>Rubric lines for the ticked responses</legend>${listing.rubric.map(item => `<label class="grading-check"><input type="checkbox" name="item" value="${h.e(item.id)}" ${group.applied.includes(item.id) ? "checked" : ""}>${h.e(display.label(item.id))} · ${h.e(item.points)} points · ${mathText(item.description, h)}</label>`).join("")}</fieldset>
      <div class="grading-contact-sheet">${group.members.map((m, n) => `<figure class="${m.state === "proposed" ? "" : "grading-contact-flagged"}"><label class="grading-check"><input type="checkbox" name="member" value="${h.e(m.id)}" checked>Response ${n + 1}${m.work_lines === null ? "" : ` · ${h.e(m.work_lines)} line${m.work_lines === 1 ? "" : "s"} of work`}</label>${m.images.map(url => cropButton(url, `Response ${n + 1} handwriting`, h)).join("")}${m.reasons.length ? `<figcaption>${m.reasons.map(r => reviewReasonMarkup(r, h)).join(" ")}</figcaption>` : ""}</figure>`).join("")}</div>
      ${h.check("I looked at every ticked response, including its work", "inspected")}`,
      "Approve the ticked responses", async (values, _, live) => {
        if (!values.has("inspected")) throw new Error("Look at every ticked response first, then confirm that you did.");
        const chosen = group.members.filter(m => values.getAll("member").includes(m.id)).map(m => ({id: m.id, version: m.version}));
        if (!chosen.length) throw new Error("Tick at least one response.");
        const result = await h.api(`/api/grading/assignments/${plan.id}/approve-group`, "POST", {question_id: questionId, group: group.id, responses: chosen,
          selected_items: values.getAll("item"), confirm_review: true, inspected_each: true});
        if (!live()) return;
        h.toast(`${result.approved} approved${result.remaining ? `; ${result.remaining} left for individual review` : ""}.`);
        h.close();
        await answerGroupsDialog(plan, questionId, h, refresh, active, true);
      });
    wide();
    const back = document.createElement("button");
    back.type = "button"; back.className = "button"; back.textContent = "← All groups";
    back.onclick = () => list();
    form.querySelector(".dialog-footer").prepend(back);
  }
  list();
}
