import assert from "node:assert/strict";
import { REMEMBER_WAIT, evidenceMarkup, followupRevisionMarkup, learnedNotice, questionMarkup, rememberedNotice, requestFollowupSuggestions, verificationMarkup } from "../static/assistant-workspace.js";
import { batchCardMarkup, cardMarkup, chatCards, conversationCards } from "../static/assistant.js";
import { chatMarkup, eventMarkup } from "../static/assistant-chat.js";

const question = questionMarkup({id: "q", questions: [{id: "0", title: '<script>Which course?</script>', options: ['" onfocus="bad()', "Second"]}]});
assert.ok(question.includes("&lt;script&gt;Which course?&lt;/script&gt;"));
assert.ok(question.includes('maxlength="4000"'));
assert.ok(question.includes("Answer and continue"));
assert.ok(!question.includes('<script>'));
const evidence = evidenceMarkup([{path: "/api/state?include=core", observed_at: "now", fingerprint: "abc", versions: {revision: 3}},
  {path: "javascript:bad()", fingerprint: "forged"},
  {kind: "catalog", path: "Catalog query", read: {query: 'SELECT "<script>"'}, fingerprint: "sql"}]);
assert.ok(evidence.includes('href="/api/state?include=core"'));
assert.ok(evidence.includes("revision"));
assert.ok(evidence.includes("SELECT"));
assert.ok(!evidence.includes("forged"));
assert.ok(!evidence.includes('<script>'));
const review = verificationMarkup({check_type: 'tool_use', status: 'review', detail: '<script>Advisory only</script>',
  checks: [{text: '<img src=x onerror=bad()>', verdict: 'uncertain'}]});
assert.ok(review.includes('Tool-use review'));
assert.ok(review.includes('Points considered · 1'));
assert.ok(review.includes('&lt;img'));
assert.ok(!review.includes('<script>'));
assert.ok(!review.includes('Evidence checked'));
const content = "Start " + "x".repeat(7000) + " Ending";
const card = cardMarkup({id: "a", pending: true, seen: 1, status: "proposed", endpoint: "assistant.artifact_create",
  body: {title: "Worksheet", filename: "work.txt", content}, preview: {data: {owner: "MATH 140"}}});
assert.ok(card.includes(content), "The complete content must be reviewable before approval.");
assert.ok(card.includes("MATH 140"));
// ONE CARD FOR ONE CHANGE (ASSISTANT-BATCHES, 2026-10-09): a one-route group's cards draw one card, once, where its first card is.
const grade = (n, extra = {}) => ({id: `g${n}`, pending: true, seen: 10 + n, status: "proposed", endpoint: "frq_grading.review", method: "POST",
  path: `/api/grading/responses/r${n}/review`, body: {selected_items: ["R7_10"], comment: "<b>credit</b>"}, summary: `Award Student ${n} <Q24> credit: 5.5 → 6/6.`, ...extra});
const graded = new Map([1, 2, 3].map((n) => [`g${n}`, grade(n)]));
graded.set("g3", grade(3, {pending: false, status: "failed", events: [{detail: {result: {error: "The grade changed meanwhile."}}}]}));
graded.set("lone", grade(9, {id: "lone", summary: "A card of its own"}));
const group = {id: "b1", title: "Exam 1 Q24: credit for parameterization work", action_ids: ["g1", "g2", "g3"], route: "frq_grading.review",
  pending: 2, states: {proposed: 2, failed: 1}};
const drawn = chatCards(graded, [group, {id: "mixed", route: null, action_ids: ["lone"]}]);
const once = drawn("g1");
assert.equal(drawn("g2"), "", "Every other card of the group draws nothing.");
assert.equal(drawn("g3"), "");
assert.ok(drawn("lone").includes("A card of its own"), "A card of a mixed group is still its own card in the chat.");
assert.equal(drawn("unknown"), null);
assert.equal((once.match(/data-batch-card=/g) || []).length, 1);
assert.ok(once.includes("Exam 1 Q24: credit for parameterization work"));
assert.ok(once.includes("3 changes · 2 wait for you · 1 failed"));
assert.ok(once.includes("Approve all 2") && once.includes("Decline all"));
assert.equal((once.match(/data-assistant-decline=/g) || []).length, 2, "Each waiting change can be left out.");
assert.ok(once.includes('data-action-id="g1" data-seen="11"') && once.includes('data-action-id="g2" data-seen="12"'));
assert.ok(!once.includes('data-action-id="g3" data-seen'), "A finished card is never approved again.");
assert.ok(once.includes("The grade changed meanwhile."));
assert.ok(once.includes("Award Student 1 &lt;Q24&gt; credit") && !once.includes("<Q24>") && !once.includes("<b>credit</b>"));
assert.ok(!once.includes("data-assistant-approve"), "No card of the group has its own Approve.");
const finished = batchCardMarkup({...group, pending: 0, states: {succeeded: 3}}, new Map([1, 2, 3].map((n) => [`g${n}`, grade(n, {pending: false, status: "succeeded"})])));
assert.ok(finished.includes('data-done="true"') && finished.includes("3 changes · 3 done") && !finished.includes("Approve all"));

// CHANGES BESIDE THE CHAT (ASSISTANT, 2026-10-10): the floating window draws a conversation's cards in a panel of their own; the
// conversation keeps one line where each was made.
const made = (id, extra = {}) => ({kind: "tool", tool: "faculty_write", label: "Prepared a change", action: {action_id: id}, ...extra});
const talk = [{kind: "user", text: "Give the credit"}, made("lone"), made("g1"), made("g2"), made("g3"), made("gone"),
  made("g1"), made("linked", {link: {link: "#gradebook", label: "Gradebook"}}), {kind: "tool", tool: "group_actions", label: "Grouped"},
  {kind: "assistant", text: "Ready."}];
const beside = conversationCards(talk, graded, [group]);
assert.equal(beside.count, 2, "One card of its own and one group: each card once, however often its action is named.");
assert.equal(beside.waiting, 3, "The lone card and the two of the group that still wait.");
assert.equal((beside.panel.match(/<article /g) || []).length, 2);
assert.ok(beside.panel.indexOf("A card of its own") < beside.panel.indexOf("data-batch-card="), "Waiting cards keep the order they were made in.");
assert.ok(beside.panel.includes('data-assistant-approve="lone"') && beside.panel.includes("Approve all 2"), "The panel holds the whole cards.");
assert.equal(beside.line("g2"), "");
assert.equal(beside.line("g3"), "");
assert.equal(beside.line("gone"), null, "An action this view has not read keeps its step line.");
assert.equal(beside.line("linked"), null);
const loneLine = beside.line("lone"), groupLine = beside.line("g1");
assert.ok(loneLine.includes('data-card-show="action-lone"') && loneLine.includes("Waits for you") && loneLine.includes("A card of its own"));
assert.ok(groupLine.includes('data-card-show="batch-b1"') && groupLine.includes("Exam 1 Q24: credit for parameterization work · 3 changes"));
assert.equal(beside.line("lone") + beside.line("g1"), "", "A card the conversation names again has no second line.");
assert.ok(!/data-assistant-(approve|decline)|data-batch-approve/.test(loneLine + groupLine), "A line never approves anything.");
for (const key of ["action-lone", "batch-b1"]) assert.ok(beside.panel.includes(`data-patch-key="${key}"`), "Each line names its card in the panel.");
const pass = conversationCards(talk, graded, [group]);  // as each drawing makes its own
const conversation = chatMarkup(talk, false, (event) => eventMarkup(event, {cards: pass.line}));
assert.equal((conversation.match(/class="assistant-card-line"/g) || []).length, 2, "The conversation holds one line a card.");
assert.ok(!conversation.includes("<article") && !conversation.includes("data-assistant-approve"), "No card is drawn in the conversation.");
const escaped = conversationCards([made("x")], new Map([["x", grade(1, {id: "x", summary: "Award <b>Lee</b> credit"})]]), []).line("x");
assert.ok(escaped.includes("Award &lt;b&gt;Lee&lt;/b&gt; credit") && !escaped.includes("<b>Lee"));
const mailed = conversationCards([made("m")], new Map([["m", grade(2, {id: "m", pending: false, status: "succeeded", endpoint: "gmail_mail.send", events: [{detail: {result: {status: "uncertain"}}}]})]]), []);
assert.ok(mailed.line("m").includes("Check delivery") && mailed.waiting === 0, "A line says what its card's chip says.");
const settled = conversationCards([made("g1")], new Map([1, 2, 3].map((n) => [`g${n}`, grade(n, {pending: false, status: "succeeded"})])), [{...group, pending: 0, states: {succeeded: 3}}]);
assert.ok(settled.line("g1").includes("Done") && settled.waiting === 0 && settled.count === 1);
// What waits is first; under it the finished cards, the latest first.
const mixed = new Map([["d1", grade(1, {id: "d1", pending: false, status: "succeeded", summary: "First done"})], ["w1", grade(2, {id: "w1", summary: "Still waiting"})],
  ["d2", grade(3, {id: "d2", pending: false, status: "declined", summary: "Second done"})]]);
const sorted = conversationCards([made("d1"), made("w1"), made("d2")], mixed, []).panel;
assert.ok(sorted.indexOf("Still waiting") < sorted.indexOf("Second done") && sorted.indexOf("Second done") < sorted.indexOf("First done"));
assert.deepEqual({...conversationCards([], graded, [group]), line: null}, {panel: "", count: 0, waiting: 0, line: null}, "A conversation without cards has no panel.");
// SEVERAL AT ONCE (ASSISTANT, 2026-10-12): follow-ups a chat prepared again in one request are receipts made at once, grouped by
// the tool itself. The conversation has one line for them and one card that lists each, the one refused with the redo's own words.
const redone = (id, status, who) => ({id, tier: "auto", status, pending: false, seen: 2, endpoint: "student_support.suggestion_redo", method: "POST",
  path: "/api/student-support/followups/suggestions/redo", page: "#follow-ups", body: {text: "Ask for a note."}, summary: `Prepare “Question” again with your changes: ${who}`,
  events: [{kind: status, detail: status === "failed" ? {status: 409, result: {error: "These suggestions are being prepared now. Ask again once they are ready."}} : {status: 202}}]});
const several = conversationCards([{kind: "tool", tool: "redo_followups", label: "Asked the follow-ups to prepare their suggestions again", action: {action_id: "r1", outcome: "done"}}],
  new Map([["r1", redone("r1", "succeeded", "Example, Alice")], ["r2", redone("r2", "succeeded", "Example, Bob")], ["r3", redone("r3", "failed", "Example, Cy")]]),
  [{id: "many", title: "Prepare 3 follow-ups again: “Ask for a note.”", action_ids: ["r1", "r2", "r3"], states: {succeeded: 2, failed: 1}, pending: 0, route: "student_support.suggestion_redo"}]);
assert.deepEqual([several.count, several.waiting], [1, 0]);
assert.ok(several.line("r1").includes("Prepare 3 follow-ups again: “Ask for a note.” · 3 changes"));
assert.ok(several.panel.includes("3 changes · 2 done, 1 failed") && !several.panel.includes("Approve all"));
assert.equal((several.panel.match(/data-action-id=/g) || []).length, 3);
assert.ok(several.panel.includes("Example, Bob") && several.panel.includes("These suggestions are being prepared now."));
console.log("assistant workspace helpers ok");

// ASSISTANT-MEMORY "A FOLLOW-UP'S REMEMBER THIS" (requirement, 2026-10-10):
// the page says exactly what was kept. A rule the Assistant wrote is quoted, says whose words it is and offers Undo.
{
  const calls = [], said = [];
  const api = async (...args) => { calls.push(args); return { undone: true }; };
  const toast = (...args) => said.push(args);
  assert.equal(rememberedNotice({ id: "r1" }, api, toast), null);  // nothing was asked to be remembered
  assert.deepEqual(rememberedNotice({ remembered: "m1", memory: { id: "m1", content: "Offer a makeup first.", ruled: false, standalone: true } }, api, toast),
    ["Saved as an Assistant preference for future drafts.", false]);
  assert.deepEqual(rememberedNotice({ remembered: "m0" }, api, toast), ["Saved as an Assistant preference for future drafts.", false]);  // an answer from before this
  const [message, options] = rememberedNotice({ remembered: "m2", memory: { id: "m2", content: "An absence to attend another course's exam review is not excused.", ruled: true, standalone: true } }, api, toast);
  assert.equal(message, "Remembered for future drafts: “An absence to attend another course's exam review is not excused.” I wrote this from what you typed, without the student.");
  assert.equal(options.action.label, "Undo");
  assert.ok(options.timeout >= 15000, "long enough to read the rule");
  await options.action.run();
  assert.deepEqual(calls, [["/api/assistant/memory/m2/undo", "POST", {}]]);
  assert.match(said[0][0], /Taken back/);
  const [fragment, shown] = rememberedNotice({ remembered: "m3", memory: { id: "m3", content: "This type of absence is not excused.", ruled: false, standalone: false } }, api, toast);
  assert.match(fragment, /Saved as you typed it\. It may not read well on its own/);
  assert.equal(shown.action, undefined);
  assert.deepEqual(rememberedNotice({ memory_error: "It is about one student, so it is not kept here." }, api, toast), ["Not saved as a preference: It is about one student, so it is not kept here.", true]);
  // A request that also remembers waits long enough for the rule to be written; one that does not waits as it always did.
  const sent = [];
  const record = async (...args) => { sent.push(args); return {}; };
  globalThis.window ??= { dispatchEvent() {} };
  globalThis.CustomEvent ??= class { constructor(type) { this.type = type; } };
  await requestFollowupSuggestions(record, { id: "s1", seen: 3, fingerprint: "f" }, "revise", { text: "This type of absence is not excused.", remember: true }, {});
  await requestFollowupSuggestions(record, { id: "s1", seen: 3, fingerprint: "f" }, "revise", { text: "Shorter." }, {});
  assert.deepEqual(sent.map((args) => args[4]), [REMEMBER_WAIT, undefined]);
  assert.ok(REMEMBER_WAIT >= 60000);
  assert.match(followupRevisionMarkup({ id: "s1", state: "ready" }, "data-followup-revise"), /written as a rule without the student when it is about this follow-up/);
}

// ASSISTANT-MEMORY "LEARNED FROM AN EDIT" (requirement, 2026-10-12): what a correction taught is said
// once, quoted, in the words of the kind of correction it was, with what it replaced and Undo.
{
  const calls = [], said = [], after = [];
  const api = async (...args) => { calls.push(args); return { undone: true }; };
  const toast = (...args) => said.push(args);
  const learned = { case: "c1", kind: "edit", memory: { id: "m5", content: "Ask plainly for documentation;\n no rule number", standing: true } };
  const [message, options] = learnedNotice(learned, api, toast, () => after.push("read again"));
  assert.equal(message, "Remembered from your edit: “Ask plainly for documentation; no rule number”.");
  assert.equal(options.action.label, "Undo");
  assert.ok(options.timeout >= 15000, "long enough to read the rule");
  await options.action.run();
  assert.deepEqual(calls, [["/api/assistant/memory/m5/undo", "POST", {}]]);
  assert.match(said[0][0], /Taken back/);
  assert.deepEqual(after, ["read again"]);
  assert.match(learnedNotice({ ...learned, kind: "decision" }, api, toast)[0], /^Remembered from your decision: “/);
  assert.equal(learnedNotice({ kind: "request", replaced: "Cite the rule.", memory: { id: "m6", content: "Never cite the rule." } }, api, toast)[0],
    "Remembered from the change you asked for: “Never cite the rule.” It replaces “Cite the rule.”.");
}
