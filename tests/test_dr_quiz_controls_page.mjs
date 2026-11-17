import assert from "node:assert/strict";
import test from "node:test";

// The DR quizzes tab's controls (SCHEDULING "DR QUIZ NOTICES", ACCOMMODATIONS "DR QUIZZES", job 005, 2026-10-15): a
// "Prescheduled in AIM" mark behind a confirm naming how many students it emails, Undo while no notice has gone out, each DR
// quiz taker's notice status and an explicit Retry behind its own confirm. Cancel posts nothing; Send posts once with a fresh
// request ID. Pure helpers only: node has no DOM.
const page = await import(new URL("../static/accommodations_page.js", import.meta.url).href);
const { noticeLabel, drQuizAction, noticesWaiting, markPrompt, retryPrompt, sendConfirmed, drQuizzesMarkup } = page;
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const REQUEST = /^[A-Za-z0-9._:-]{1,100}$/;

const quiz = (changes = {}) => ({ key: "i1:s1", item: "i1", title: "Quiz 5", assignment_id: null, section_id: "s1", section: 507, date: "2026-10-23",
  time: "8:00 AM", start: "08:00", end: "", place: "HELD 107", time_source: "planned", minutes: 20, ready: true, reason: "",
  aim: { status: "not_in_aim", bookings: 0, dates: [] }, ...changes });
const view = (changes = {}) => ({ term_id: "t1", code: "MATH 140", term_label: "Fall 2026", today: "2026-10-12",
  sections: [{ section_id: "s1", section: 507, dr_students: 1 }], quizzes: [quiz()], fingerprint: "f".repeat(64), ...changes });
const student = (changes = {}) => ({ name: "Ana Díaz", status: null, detail: "", batch: null, ...changes });
const entry = (changes = {}) => ({ key: "i1:s1", state: "unmarked", mark: null, students: [student()], ...changes });
const MARK = { date: "2026-10-23", start: "08:00", actor: "instructor", at: "2026-10-15T12:00:00Z" };
const notices = (...quizzes) => ({ term_id: "t1", quizzes });
const count = (html, pattern) => (html.match(pattern) || []).length;

test("noticeLabel words each notice status with a shared chip tone, and nothing for no status", () => {
  assert.deepEqual(noticeLabel(student({ status: "accepted", batch: "b1" })), { text: "Told", tone: "success" });
  assert.deepEqual(noticeLabel(student({ status: "waiting" })), { text: "Waiting to send", tone: "info" });
  assert.deepEqual(noticeLabel(student({ status: "skipped", detail: "no email address on the roster" })),
    { text: "Skipped · no email address on the roster", tone: "neutral" });
  assert.deepEqual(noticeLabel(student({ status: "skipped", detail: "dropped" })), { text: "Skipped · dropped", tone: "neutral" });
  assert.deepEqual(noticeLabel(student({ status: "failed", detail: "550 refused", batch: "b2" })), { text: "Failed", tone: "danger" });
  assert.deepEqual(noticeLabel(student({ status: "uncertain", detail: "timed out", batch: "b3" })), { text: "Uncertain", tone: "warning" });
  assert.equal(noticeLabel(student({ status: null })), null);
});

test("drQuizAction builds the mark, unmark and retry posts with the term ID encoded", () => {
  const term = "t 1/ü";
  const base = `/api/scheduling/terms/${encodeURIComponent(term)}/aim-prescheduling`;
  assert.deepEqual(drQuizAction("mark", term, "i1:s1", "f".repeat(64), "r1"),
    { path: `${base}/mark`, body: { request_id: "r1", key: "i1:s1", fingerprint: "f".repeat(64) } });
  assert.deepEqual(drQuizAction("unmark", term, "i1:s1", "f".repeat(64), "r2"),
    { path: `${base}/unmark`, body: { request_id: "r2", key: "i1:s1" } });
  assert.deepEqual(drQuizAction("retry", term, "batch-9", "f".repeat(64), "r3"),
    { path: `${base}/retry`, body: { request_id: "r3", batch: "batch-9" } });
  assert.equal(drQuizAction("mark", "t1", "k", "fp", "r").path, "/api/scheduling/terms/t1/aim-prescheduling/mark");
});

test("noticesWaiting is true only while some student of some quiz waits", () => {
  assert.equal(noticesWaiting(null), false);
  assert.equal(noticesWaiting(notices()), false);
  assert.equal(noticesWaiting(notices(entry({ students: [] }))), false);
  assert.equal(noticesWaiting(notices(entry({ state: "marked", mark: MARK, students: [
    student({ status: "accepted", batch: "b" }), student({ status: "skipped", detail: "dropped" }),
    student({ status: "failed", batch: "c" }), student({ status: "uncertain", batch: "d" }), student()] }))), false);
  assert.equal(noticesWaiting(notices(entry(), entry({ key: "i2:s1", state: "marked", mark: MARK, students: [
    student({ status: "accepted", batch: "b" }), student({ name: "Bo", status: "waiting" })] }))), true);
});

test("markPrompt counts the students the mark will email (everyone not skipped)", () => {
  const four = entry({ students: [student({ name: "A" }), student({ name: "B" }), student({ name: "C" }),
    student({ name: "D", status: "skipped", detail: "no email address on the roster" })] });
  assert.deepEqual(markPrompt(four), { title: "Prescheduled in AIM?", body: "This emails 3 students.", send: "Send" });
  const one = entry({ students: [student({ name: "A" }), student({ name: "D", status: "skipped", detail: "dropped" })] });
  assert.equal(markPrompt(one).body, "This emails 1 student.");
  assert.equal(markPrompt(one).send, "Send");
  assert.equal(markPrompt(one).title, "Prescheduled in AIM?");
  const changed = entry({ state: "changed", mark: MARK, students: [student({ name: "A", status: "accepted", batch: "b" }), student({ name: "B" })] });
  assert.equal(markPrompt(changed).body, "This emails 2 students.");
});

test("retryPrompt names the student raw (markup escapes it)", () => {
  assert.deepEqual(retryPrompt(student({ status: "failed", batch: "b1", detail: "550" })),
    { title: "Send this email again to Ana Díaz?", body: "", send: "Send" });
  assert.equal(retryPrompt(student({ name: "O'Brien <x>", status: "uncertain", batch: "b" })).title, "Send this email again to O'Brien <x>?");
});

function fakes(answer, { reply = { term_id: "t1", quizzes: [] } } = {}) {
  const calls = { ask: [], api: [], newId: 0 };
  return {
    calls,
    deps: {
      ask: async (prompt) => { calls.ask.push(prompt); return answer; },
      api: async (...args) => { calls.api.push(args); return reply; },
      newId: () => { calls.newId += 1; return `id-${calls.newId}`; },
    },
  };
}

const markRequest = { action: "mark", termId: "t1", target: "i1:s1", fingerprint: "f".repeat(64) };
const retryRequest = { action: "retry", termId: "t1", target: "batch-7", fingerprint: "f".repeat(64) };

test("Cancel posts nothing and asks for no request ID, for a mark and for a retry", async () => {
  for (const [prompt, request] of [[markPrompt(entry()), markRequest], [retryPrompt(student({ status: "failed", batch: "batch-7" })), retryRequest]]) {
    for (const answer of [false, undefined, null]) {
      const { calls, deps } = fakes(answer);
      assert.equal(await sendConfirmed(prompt, request, deps), null);
      assert.deepEqual(calls.ask, [prompt], "the confirm was shown with this prompt");
      assert.equal(calls.api.length, 0, "nothing posted");
      assert.equal(calls.newId, 0, "no request ID taken");
    }
  }
});

test("Send on a mark posts once: the drQuizAction path and body with the fingerprint and the new request ID", async () => {
  const reply = notices(entry({ state: "marked", mark: MARK, students: [student({ status: "waiting" })] }));
  const { calls, deps } = fakes(true, { reply });
  const prompt = markPrompt(entry());
  assert.equal(await sendConfirmed(prompt, markRequest, deps), reply, "answers the status read");
  assert.equal(calls.newId, 1);
  assert.equal(calls.api.length, 1, "posted exactly once");
  const expected = drQuizAction("mark", "t1", "i1:s1", "f".repeat(64), "id-1");
  assert.deepEqual(calls.api[0], [expected.path, "POST", expected.body]);
  assert.equal(calls.api[0][2].request_id, "id-1");
  assert.equal(calls.api[0][2].fingerprint, "f".repeat(64));
  assert.equal(calls.api[0][2].key, "i1:s1");
});

test("Send on a retry posts once with the batch and the new request ID", async () => {
  const { calls, deps } = fakes(true);
  await sendConfirmed(retryPrompt(student({ status: "failed", batch: "batch-7" })), retryRequest, deps);
  assert.equal(calls.api.length, 1);
  const [path, method, body] = calls.api[0];
  assert.equal(method, "POST");
  assert.equal(path, "/api/scheduling/terms/t1/aim-prescheduling/retry");
  assert.deepEqual(body, { request_id: "id-1", batch: "batch-7" });
});

test("two Sends with the default request IDs post two different valid IDs", async () => {
  const posted = [];
  const deps = { ask: async () => true, api: async (path, method, body) => { posted.push(body); return {}; } };
  await sendConfirmed(markPrompt(entry()), markRequest, deps);
  await sendConfirmed(markPrompt(entry()), markRequest, deps);
  assert.equal(posted.length, 2);
  for (const body of posted) assert.match(body.request_id, REQUEST);
  assert.notEqual(posted[0].request_id, posted[1].request_id);
});

test("without a status read the markup stays read-only and carries no controls", () => {
  const html = drQuizzesMarkup(view(), escape, null);
  assert.ok(!/<button|<form|<input/i.test(html), "read-only");
  assert.ok(!html.includes("data-dr-"), "no control hooks");
});

test("a ready, unmarked quiz gets the mark button in a Prescheduled column; a not-ready quiz gets none", () => {
  const html = drQuizzesMarkup(view({ term_id: "t1", quizzes: [quiz(), quiz({ key: "i2:s1", item: "i2", title: "Quiz 9", date: "", time: "",
    start: "", ready: false, reason: "No date yet" })] }), escape, notices(entry(), entry({ key: "i2:s1" })));
  assert.ok(html.includes(">Prescheduled<"), "the column is headed Prescheduled");
  assert.ok(html.includes('data-dr-mark="i1:s1"'), html);
  assert.ok(!html.includes('data-dr-mark="i2:s1"'), "not ready: no mark button");
  assert.equal(count(html, /data-dr-mark=/g), 1);
  assert.ok(html.includes('data-dr-term="t1"'));
  assert.ok(/<button[^>]*data-dr-mark="i1:s1"[^>]*>[\s\S]*?Prescheduled in AIM[\s\S]*?<\/button>/.test(html), "a button reading Prescheduled in AIM");
  const button = html.match(/<button[^>]*data-dr-mark="i1:s1"[^>]*>/)[0];
  const label = (button.match(/aria-label="([^"]*)"/) || [])[1] || "";
  assert.ok(label.includes("Quiz 5") && label.includes("507"), `aria-label names quiz and section: ${label}`);
  assert.ok(!html.includes("data-dr-unmark"), "nothing to undo");
});

test("a marked quiz shows the chip and Undo only while no notice has gone out", () => {
  const marked = (students) => drQuizzesMarkup(view(), escape, notices(entry({ state: "marked", mark: MARK, students })));
  const quiet = marked([student({ status: "waiting" }), student({ name: "Bo", status: "skipped", detail: "dropped" })]);
  assert.ok(quiet.includes("Prescheduled in AIM"), "the marked chip");
  assert.ok(/class="chip"[^>]*data-tone="success"[^>]*>\s*Prescheduled in AIM|data-tone="success"[^>]*>\s*Prescheduled in AIM/.test(quiet), "a success chip");
  assert.ok(quiet.includes('data-dr-unmark="i1:s1"'), "Undo while nothing went out");
  assert.ok(/data-dr-unmark="i1:s1"[^>]*>\s*Undo/.test(quiet) || /<button[^>]*data-dr-unmark[\s\S]*?Undo/.test(quiet));
  assert.ok(!quiet.includes("data-dr-mark="), "a marked quiz has no mark button");
  for (const status of ["accepted", "failed", "uncertain"]) {
    const sent = marked([student({ status, batch: "b1", detail: "x" }), student({ name: "Bo", status: "waiting" })]);
    assert.ok(!sent.includes("data-dr-unmark"), `no Undo once a student is ${status}`);
  }
});

test("only failed and uncertain students get Retry, carrying their batch", () => {
  const html = drQuizzesMarkup(view(), escape, notices(entry({ state: "marked", mark: MARK, students: [
    student({ name: "Ana", status: "accepted", batch: "b-acc" }),
    student({ name: "Bo", status: "failed", batch: "b-fail", detail: "550 mailbox refused" }),
    student({ name: "Cy", status: "uncertain", batch: "b-unc", detail: "connection reset" }),
    student({ name: "Di", status: "waiting" }),
    student({ name: "Ed", status: "skipped", detail: "no email address on the roster" })] })));
  assert.ok(html.includes('data-dr-retry="b-fail"') && html.includes('data-dr-retry="b-unc"'));
  assert.ok(!html.includes('data-dr-retry="b-acc"'));
  assert.equal(count(html, /data-dr-retry=/g), 2);
  assert.ok(/<button[^>]*data-dr-retry="b-fail"[^>]*>[\s\S]*?Retry/.test(html), "Retry is a button");
  assert.ok(html.includes('data-dr-name="Bo"') && html.includes('data-dr-name="Cy"'));
  for (const text of ["Told", "Failed", "Uncertain", "Waiting to send", "Skipped · no email address on the roster", "550 mailbox refused", "connection reset"]) {
    assert.ok(html.includes(text), text);
  }
  for (const name of ["Ana", "Bo", "Cy", "Di", "Ed"]) assert.ok(html.includes(name), name);
  assert.ok(html.includes('data-tone="danger"') && html.includes('data-tone="warning"') && html.includes('data-tone="info"'));
});

test("a changed quiz warns that the date or time moved and offers the mark again", () => {
  const html = drQuizzesMarkup(view(), escape, notices(entry({ state: "changed", mark: MARK, students: [student({ status: "accepted", batch: "b" })] })));
  assert.ok(html.includes("Date or time changed since marked"));
  assert.ok(html.includes('data-dr-mark="i1:s1"'));
  const notReady = drQuizzesMarkup(view({ quizzes: [quiz({ ready: false, start: "", time: "", reason: "No time yet" })] }), escape,
    notices(entry({ state: "changed", mark: MARK })));
  assert.ok(notReady.includes("Date or time changed since marked"));
  assert.ok(!notReady.includes("data-dr-mark="), "not ready: no mark button");
});

test("names and details are escaped, in text and in the confirm's data-dr-name", () => {
  const html = drQuizzesMarkup(view(), escape, notices(entry({ state: "marked", mark: MARK, students: [
    student({ name: `<i>Zoë & "Q"</i>`, status: "failed", batch: "b1", detail: "<script>bad</script>" }),
    student({ name: "<b>Bo</b>", status: "skipped", detail: "<u>dropped</u>" })] })));
  assert.ok(!html.includes("<i>Zoë") && !html.includes("<script>") && !html.includes("<b>Bo") && !html.includes("<u>dropped"), html);
  assert.ok(html.includes("&lt;i&gt;Zoë &amp; &quot;Q&quot;&lt;/i&gt;"));
  assert.ok(html.includes("&lt;script&gt;bad&lt;/script&gt;"));
  assert.ok(html.includes("&lt;b&gt;Bo&lt;/b&gt;"));
  assert.ok(html.includes("Skipped · &lt;u&gt;dropped&lt;/u&gt;"));
  assert.ok(html.includes('data-dr-name="&lt;i&gt;Zoë &amp; &quot;Q&quot;&lt;/i&gt;"'));
});
