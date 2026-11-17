// The Assistant's shared conversation (static/assistant-chat.js): one drawing for the drawer and the floating window, the live
// step list, what can be done with an answer, the chips, the queued message and the send that is never repeated.
import assert from "node:assert/strict";
import test from "node:test";
import { askAgainAllowed, chatMarkup, chatTitle, chipsMarkup, clockText, composerMarkup, contextChips, elapsedSeconds, elapsedText,
  emptyChatMarkup, eventMarkup, liveStepsMarkup, queuedMarkup, sendMessage, startersFor, turnsOf } from "../static/assistant-chat.js";

const at = (seconds) => `2026-10-09T14:05:${String(seconds).padStart(2, "0")}-05:00`;
const read = (label, extra = {}) => ({ kind: "tool", tool: "x", label, ...extra });

test("a running turn lists its finished steps and the line it is on", () => {
  assert.equal(liveStepsMarkup([]), '<p class="assistant-tool assistant-working" role="status">Working…</p>');
  const steps = ["one", "two", "three", "four", "five", "six", "seven"].map((label) => read(`Read ${label}`));
  const live = liveStepsMarkup(steps);
  assert.match(live, /^<ol class="assistant-live" aria-label="Steps so far"><li class="assistant-live-earlier">2 earlier steps<\/li><li>Read three<\/li>/);
  assert.match(live, /<li>Read seven<\/li><\/ol><p class="assistant-tool assistant-working" role="status">Working…<\/p>$/);
  assert.equal((live.match(/<li>/g) || []).length, 5);
  // A check's own words are the current line while it runs; the reads stay listed.
  const checking = liveStepsMarkup([read("Read <inbox>"), { kind: "status", step: true, text: "Checking the answer against its sources…" }]);
  assert.match(checking, /<li>Read &lt;inbox&gt;<\/li><\/ol><p class="assistant-tool assistant-working" role="status">Checking the answer against its sources…<\/p>$/);
  assert.match(liveStepsMarkup(Array.from({ length: 6 }, (_, i) => read(`Read ${i}`))), /1 earlier step</);
});

test("the fold says how long the turn took, by the server's stamps", () => {
  assert.equal(elapsedSeconds([{ at: at(1) }, { at: at(24) }]), 23);
  assert.equal(elapsedSeconds([{ at: at(1) }]), null);
  assert.equal(elapsedSeconds([{}, {}]), null);
  assert.equal(elapsedText(23), "23 s");
  assert.equal(elapsedText(65), "1 min 5 s");
  assert.equal(elapsedText(120), "2 min");
  assert.equal(elapsedText(754), "12 min");
  assert.equal(clockText(7), "0:07");
  assert.equal(clockText(760), "12:40");
  const turn = [{ kind: "user", text: "Scans?", at: at(1) }, { ...read("Read inbox"), at: at(4) }, { kind: "assistant", text: "Two.", at: at(24) }];
  const said = (event) => event.kind === "assistant" ? "<a>" : event.kind === "user" ? "<u>" : "";
  assert.match(chatMarkup(turn, false, said), /<span>How it got this<\/span><span class="disclosure-hint">1 step · 23 s<\/span>/);
});

test("Copy, Copy table and Ask again sit under an answered turn", () => {
  const said = () => "";
  const plain = [{ kind: "user", text: "Hi" }, { kind: "assistant", text: "Hello." }];
  assert.equal(chatMarkup(plain, false, said), "");
  assert.equal(chatMarkup(plain, false, said, { actions: true }),
    '<p class="assistant-turn-actions" data-chat-turn="0"><button class="text-button" type="button" data-chat-copy>Copy</button><button class="text-button" type="button" data-chat-again>Ask again</button></p>');
  const table = [{ kind: "user", text: "Scores" }, { kind: "assistant", text: "| A |\n|---|\n| 1 |" }];
  assert.match(chatMarkup(table, false, said, { actions: true }), /data-chat-copy>Copy<\/button><button class="text-button" type="button" data-chat-copy-table>Copy table<\/button>/);
  // Only the last turn offers Ask again; a running turn offers nothing.
  const two = chatMarkup([...plain, ...table], false, said, { actions: true });
  assert.equal((two.match(/data-chat-again/g) || []).length, 1);
  assert.match(two, /data-chat-turn="1"><button[^>]*data-chat-copy>Copy<\/button><button[^>]*data-chat-copy-table>Copy table<\/button><button[^>]*data-chat-again>/);
  assert.doesNotMatch(chatMarkup(plain.slice(0, 1), true, said, { actions: true }), /assistant-turn-actions/);
  // No answer, nothing to copy.
  assert.doesNotMatch(chatMarkup([{ kind: "user", text: "Hi" }, { kind: "error", text: "Stopped" }], false, said, { actions: true }), /assistant-turn-actions/);
});

test("Ask again is offered only where it cannot repeat a change or need what is gone", () => {
  const user = { kind: "user", text: "Who is missing Quiz 4?" };
  assert.equal(askAgainAllowed([user, { kind: "assistant", text: "Four." }]), true);
  assert.equal(askAgainAllowed([user, read("Read gradebook"), { kind: "assistant", text: "Four." }]), true);
  assert.equal(askAgainAllowed([user, read("Change a grade", { action: { action_id: "a1" } })]), false);
  assert.equal(askAgainAllowed([user, { kind: "tool", tool: "group_actions", label: "Grouped" }]), false);
  assert.equal(askAgainAllowed([{ kind: "receipt", text: "Results" }, { kind: "assistant", text: "Done." }]), false);
  assert.equal(askAgainAllowed([{ ...user, capture: { id: "c1" } }]), false);
  assert.equal(askAgainAllowed([{ ...user, review_only: true }]), false);
  assert.equal(askAgainAllowed([{ ...user, files: [{ name: "x.pdf" }] }]), false);
  assert.equal(askAgainAllowed([{ kind: "user", text: "  " }]), false);
});

test("one drawing serves the drawer and the floating window", () => {
  const cards = (id) => id === "a1" ? "<card>" : null;
  // The instructor's own lines keep their breaks; the time comes from the event's campus wall clock.
  const clock = { now: new Date("2026-10-09T20:00:00Z"), zone: "America/Chicago" };
  const own = eventMarkup({ kind: "user", text: "First <line>\nsecond", at: "2026-10-09T14:05:00-05:00" }, clock);
  assert.equal(own, '<div class="assistant-msg" data-from="you"><div class="assistant-msg-meta"><span class="assistant-speaker">You</span><time datetime="2026-10-09T14:05:00-05:00">2:05 PM</time></div><p>First &lt;line&gt;<br>second</p></div>');
  const last = { kind: "user", text: "Again" };
  assert.match(eventMarkup(last, { edit: last }), /<div class="assistant-msg-meta"><span class="assistant-speaker">You<\/span><button class="text-button" type="button" data-chat-edit>Edit<\/button><\/div>/);
  assert.doesNotMatch(eventMarkup(last, { edit: { kind: "user", text: "Again" } }), /data-chat-edit/);
  assert.match(eventMarkup({ kind: "user", text: "Check", review_only: true }), /Review only · every change waits for Approve/);
  // A selection: the drawer shows what was selected inside the message; the window shows it above the conversation instead.
  const capture = { id: "c1", url: "/api/assistant/conversations/x/captures/c1", captured_at: "2026-10-09T14:00:00Z", selection: { text: "Row <7>" } };
  const selected = { kind: "user", text: "ignored", capture };
  const inDrawer = eventMarkup(selected, { capture });
  assert.match(inDrawer, /<p>Analyze this selected area<\/p><details class="disclosure"><summary>Selected content<\/summary><pre class="assistant-selected-text">Row &lt;7&gt;<\/pre><\/details><figure><img data-capture-image src="\/api\/assistant\/conversations\/x\/captures\/c1"/);
  assert.equal(eventMarkup(selected, { capture, selection: false }), '<div class="assistant-msg" data-from="you"><div class="assistant-msg-meta"><span class="assistant-speaker">You</span></div><p>Analyze this selected area</p></div>');
  assert.doesNotMatch(eventMarkup(selected, { edit: selected }), /data-chat-edit/);
  // An answer is marked as finished content: the same answer keeps its mark, another gets another.
  const answer = eventMarkup({ kind: "assistant", seq: 7, text: "It is **7**." });
  assert.match(answer, /^<div class="assistant-msg" data-from="assistant" data-patch-static="7-[0-9a-z-]+"><div class="assistant-speaker">Assistant<\/div><p>It is <strong>7<\/strong>\.<\/p><\/div>$/);
  assert.equal(answer, eventMarkup({ kind: "assistant", seq: 7, text: "It is **7**." }));
  assert.notEqual(answer, eventMarkup({ kind: "assistant", seq: 7, text: "It is **8**." }));
  // A failure is a callout on both surfaces; a failed step stays in view; a card is drawn where its step is.
  assert.match(eventMarkup({ kind: "error", text: "The <model> stopped" }), /class="callout" data-tone="danger" role="alert"><strong class="callout-title">The assistant stopped<\/strong><p>The &lt;model&gt; stopped<\/p>/);
  assert.equal(eventMarkup(read("Read inbox", { ok: false })), '<p class="assistant-tool" data-ok="false">Read inbox</p>');
  assert.equal(eventMarkup(read("Prepared", { action: { action_id: "a1" } }), { cards }), "<card>");
  assert.equal(eventMarkup(read("Open Inbox", { link: { link: "#inbox", label: "Open <Inbox>" } })), '<p class="assistant-tool"><a class="link-arrow assistant-route" href="#inbox">Open &lt;Inbox&gt;</a></p>');
  assert.equal(eventMarkup({ kind: "status", text: "Stopped." }), '<p class="assistant-tool">Stopped.</p>');
  assert.equal(eventMarkup({ kind: "question" }), "");
});

test("a chat is named by its first message", () => {
  assert.equal(chatTitle([]), "");
  assert.equal(chatTitle([{ kind: "status", text: "x" }, { kind: "user", text: "  Who is\nmissing Quiz 4?  " }]), "Who is missing Quiz 4?");
  assert.equal(chatTitle([{ kind: "user", text: "ignored", capture: { id: "c" } }]), "Selected area");
  const long = chatTitle([{ kind: "user", text: "Please list every student in section 507 who has not yet turned in the fourth quiz, with their absence" }]);
  assert.equal(long, "Please list every student in section 507 who has not yet…");
  assert.ok(long.length <= 61);
  assert.deepEqual(turnsOf([{ kind: "user" }, { kind: "tool" }, { kind: "receipt" }, { kind: "assistant" }, { kind: "answer" }]).map((turn) => turn.length), [2, 2, 1]);
});

test("an empty chat offers the page's starters, which only fill the box", () => {
  assert.equal(startersFor("#gradebook/t1?section=s1")[0], "Who is missing work in this gradebook?");
  assert.equal(startersFor("#follow-ups").length, 3);
  assert.deepEqual(startersFor("#nowhere"), startersFor(""));
  const empty = emptyChatMarkup("#mail");
  assert.match(empty, /<h3>Ask Faculty<\/h3>/);
  assert.equal((empty.match(/data-chat-starter/g) || []).length, 3);
  assert.match(empty, /<button class="button" type="button" data-chat-starter>Summarize my unread student email\.<\/button>/);
});

test("chips say what goes with the next message", () => {
  const route = { term_id: "t1" };
  // The page alone: named, never removable.
  assert.deepEqual(contextChips({ page: "Gradebook", record: route, route }).map((chip) => [chip.key, chip.label, Boolean(chip.remove)]), [["page", "Gradebook page", false]]);
  assert.deepEqual(contextChips({ record: route, route }), []);
  // A selected cell adds a removable record chip; its value changes with the cell, so a removal lasts only for that cell.
  const cell = contextChips({ page: "Gradebook", record: { term_id: "t1", row: "s1:e1", section_id: "s1", enrollment_id: "e1", item_id: "c4" }, route });
  assert.deepEqual(cell.map((chip) => chip.label), ["Gradebook page", "Selected student and column"]);
  assert.equal(cell[1].value, JSON.stringify({ row: "s1:e1", section_id: "s1", enrollment_id: "e1", item_id: "c4" }));
  assert.equal(contextChips({ record: { enrollment_id: "e1", source_key: "k" }, route: {} })[0].label, "This follow-up");
  assert.equal(contextChips({ record: { enrollment_id: "e1" }, route: {} })[0].label, "Selected student");
  const picked = contextChips({ selection: "x".repeat(80) + " <b>" });
  assert.equal(picked[0].label, `Selected text “${"x".repeat(60)}…”`);
  const html = chipsMarkup([{ key: "page", label: "Grade <book>" }, { key: "record", label: "Selected student", remove: "Do not send the selected record" }]);
  assert.match(html, /<span class="assistant-chip" data-chip="page"><span>Grade &lt;book&gt;<\/span><\/span>/);
  assert.match(html, /data-chip-remove="record" aria-label="Do not send the selected record">×<\/button>/);
});

test("the composer is one markup with each surface's own hooks", () => {
  const html = composerMarkup({ id: "assistant-text", label: "Message the assistant", placeholder: "Ask <it>", icon: (name) => `[${name}]`,
    hooks: { attached: "data-assistant-attached", state: "data-assistant-state", stop: 'data-assistant="stop"' } });
  assert.match(html, /<label class="sr-only" for="assistant-text">Message the assistant<\/label>/);
  assert.match(html, /<div class="assistant-chips" data-chat-chips hidden><\/div><div data-chat-queued hidden><\/div><div data-assistant-attached hidden><\/div>/);
  assert.match(html, /<textarea id="assistant-text" name="text" rows="2" maxlength="20000" placeholder="Ask &lt;it&gt;"><\/textarea>/);
  assert.match(html, /\[attach\].*<span class="assistant-state" data-assistant-state><\/span><button class="button" type="button" data-assistant="stop" hidden>Stop<\/button><button class="button primary" type="submit">Send<\/button>/s);
  assert.equal(queuedMarkup(null), "");
  const waiting = queuedMarkup({ text: "Then   email <them>\nplease", files: 2 });
  assert.match(waiting, /<strong>Sends when this answer finishes:<\/strong> Then email &lt;them&gt; please · 2 files<\/span>/);
  assert.match(waiting, /data-chat-queued-edit>Edit<\/button><button class="text-button" type="button" data-chat-queued-remove>Remove<\/button>/);
  assert.match(queuedMarkup({ text: "", files: 1 }), /<\/strong> 1 file<\/span>/);
});

test("a message is sent once: a lost response is settled by one read, never resent", async () => {
  const calls = [];
  const failing = (status, view) => async (path, method = "GET") => {
    calls.push(`${method} ${path}`);
    if (method === "POST") throw Object.assign(new Error("lost"), { status });
    if (view instanceof Error) throw view;
    return view;
  };
  const body = { text: "Who is missing?" };
  // Accepted: nothing else is asked.
  const ok = await sendMessage(async (path, method) => { calls.push(`${method} ${path}`); return { id: "c1", busy: true }; }, "/api/assistant", "c1", body, 4);
  assert.deepEqual([ok.sent, ok.view.busy, calls.splice(0)], [true, true, ["POST /api/assistant/conversations/c1/messages"]]);
  // A lost response whose turn did start: read once, treated as sent.
  const ran = await sendMessage(failing(503, { events: [{ kind: "user", seq: 5, text: "Who is missing?" }], busy: true }), "/api/assistant", "c1", body, 4);
  assert.equal(ran.sent, true);
  assert.deepEqual(calls.splice(0), ["POST /api/assistant/conversations/c1/messages", "GET /api/assistant/conversations/c1?after=4"]);
  // A lost response whose turn did not start: the error comes back marked uncertain, with what was read; still one POST.
  await assert.rejects(sendMessage(failing(0, { events: [], busy: false }), "/api/assistant", "c1", body, 4), (error) => error.uncertain === true && error.view.busy === false);
  assert.deepEqual(calls.splice(0), ["POST /api/assistant/conversations/c1/messages", "GET /api/assistant/conversations/c1?after=4"]);
  // An earlier message with the same words does not count: only one after `before`.
  await assert.rejects(sendMessage(failing(502, { events: [{ kind: "user", seq: 4, text: "Who is missing?" }] }), "/api/assistant", "c1", body, 4), (error) => error.uncertain === true);
  calls.splice(0);
  // Still answering (409): known not to have run, so not "uncertain"; the read still shows where the chat is.
  await assert.rejects(sendMessage(failing(409, { events: [], busy: true }), "/api/assistant", "c1", body, 4), (error) => error.uncertain === false && error.view.busy === true);
  calls.splice(0);
  // A definite refusal or a forgotten conversation: no read at all, the draft stays with the caller.
  for (const status of [400, 404, 410, 413]) {
    await assert.rejects(sendMessage(failing(status, {}), "/api/assistant", "c1", body, 4), (error) => error.status === status && error.uncertain === undefined);
    assert.deepEqual(calls.splice(0), ["POST /api/assistant/conversations/c1/messages"]);
  }
  // Files alone take the server's words: any own message since then is it. A failed read leaves it uncertain.
  assert.equal((await sendMessage(failing(500, { events: [{ kind: "user", seq: 9, text: "Look at the file I attached." }] }), "/api/assistant", "c1", { text: "", files: ["f"] }, 4)).sent, true);
  await assert.rejects(sendMessage(failing(500, new Error("down")), "/api/assistant", "c1", body, 4), (error) => error.uncertain === true && error.view === null);
});

// PICKERS (static/assistant-pickers.js): "/" at the start of the box and "@" before a name; a choice only writes into the box.
const { trigger, slashItems, mentionItems, mentionedRecord, pickerMarkup } = await import("../static/assistant-pickers.js");

test("the pickers open only where a choice is meant", () => {
  assert.deepEqual(trigger("/who", 4), { kind: "/", query: "who", start: 0, end: 4 });
  // The whole box is replaced, whatever follows the caret on that line.
  assert.deepEqual(trigger("/who is", 4), { kind: "/", query: "who", start: 0, end: 7 });
  assert.equal(trigger("see /api/x", 10), null);
  assert.equal(trigger("first\n/who", 10), null);
  assert.deepEqual(trigger("ask @Ad", 7), { kind: "@", query: "Ad", start: 4, end: 7 });
  assert.deepEqual(trigger("@Ada Sam", 8), { kind: "@", query: "Ada Sam", start: 0, end: 8 });
  // An email address, a second "@", a line break or a long tail is not a name being picked.
  assert.equal(trigger("ada@tamu.edu", 12), null);
  assert.equal(trigger("@a @", 4)?.query, "");
  assert.equal(trigger("@Ada\nmore", 9), null);
  assert.equal(trigger(`@${"x".repeat(31)}`, 32), null);
  assert.equal(trigger("@ Ada", 5), null);
  assert.equal(trigger("plain words", 11), null);
});

test("the slash list is the page's starters, a few for anywhere, then the instructor's own messages, each once", () => {
  const items = slashItems("#gradebook/t1", ["What needs me today?", "  My own question  ", ""]);
  assert.deepEqual(items.slice(0, 3).map((item) => item.group), ["For this page", "For this page", "For this page"]);
  assert.equal(items[0].insert, "Who is missing work in this gradebook?");
  assert.deepEqual(items.filter((item) => item.group === "Your recent messages").map((item) => item.label), ["My own question"]);
  assert.equal(items.filter((item) => item.label === "What needs me today?").length, 1);
  assert.equal(new Set(items.map((item) => item.label)).size, items.length);
});

test("an @ choice names a student or a section and carries only the IDs that name it", () => {
  const source = { groups: [{ code: "MATH 140", term_label: "Fall 2026", term_id: "t1", students: [
    { name: "Ada <Sample>", section_id: "s507", enrollment_id: "e1", sections: [507] },
    { name: "Ben Sample", section_id: "s507", enrollment_id: "e2", sections: [507], dropped: true },
    { name: "Cy Sample", section_id: "s508", enrollment_id: "e3", sections: [508] }] }] };
  const items = mentionItems(source);
  assert.deepEqual(items.map((item) => item.label), ["Ada <Sample>", "Ben Sample", "Cy Sample", "MATH 140 section 507", "MATH 140 section 508"]);
  assert.deepEqual(items[0].record, { term_id: "t1", section_id: "s507", enrollment_id: "e1" });
  assert.match(items[1].detail, /Section 507 · Dropped$/);
  assert.deepEqual(items[3].record, { term_id: "t1", section_id: "s507" });
  assert.deepEqual(mentionItems(null), []);
  // Only names still in the message count; a later name of the same kind replaces an earlier one.
  const mentions = [{ text: "Ada Sample", record: items[0].record }, { text: "Cy Sample", record: items[2].record }];
  assert.deepEqual(mentionedRecord(mentions, "Compare Ada Sample with Cy Sample"), { term_id: "t1", section_id: "s508", enrollment_id: "e3" });
  assert.deepEqual(mentionedRecord(mentions, "Only Ada Sample now"), items[0].record);
  assert.deepEqual(mentionedRecord(mentions, "Nobody named"), {});
  const html = pickerMarkup(items.slice(0, 2).concat(items[3]), 1, "box-picker");
  assert.match(html, /^<li class="assistant-picker-group" role="presentation">Students<\/li><li role="option" id="box-picker-0" data-pick="0" aria-selected="false"><span>Ada &lt;Sample&gt;<\/span><small>MATH 140 · Fall 2026 · Section 507<\/small><\/li>/);
  assert.match(html, /id="box-picker-1" data-pick="1" aria-selected="true"/);
  assert.match(html, /<li class="assistant-picker-group" role="presentation">Sections<\/li><li role="option" id="box-picker-2"/);
});
