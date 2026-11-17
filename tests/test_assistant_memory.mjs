// ASSISTANT MEMORY (requirement, 2026-10-10): the chat's note and what an answer was given. The list
// itself is the Memory page's (tests/test_memory_settings.mjs).
import assert from "node:assert/strict";
import test from "node:test";
import { memoriesGiven, memoriesGivenMarkup, memoryHref, memoryNote, memoryReworded, memoryUndone } from "../static/assistant-memory.js";
import { eventMarkup, isStep, turnMarkup, turnsOf } from "../static/assistant-chat.js";

const kept = { id: "m1", content: "Always put an answer key at the end of a quiz.", scope: "workspace", applies_to: "Everything", standalone: true };

test("a kept statement says what was remembered and offers Undo, once", () => {
  const note = memoryNote({ kind: "memory", action: "kept", memory: kept });
  assert.match(note, /Remembered: “Always put an answer key at the end of a quiz\.”/);
  assert.match(note, /data-memory-undo="m1"/);
  assert.match(note, /data-tone="kept"/);
  const events = [{ kind: "memory", action: "kept", memory: kept }, { kind: "memory", action: "undone", memory: { id: "m1" } }];
  const undone = memoryUndone(events);
  assert.deepEqual([...undone], ["m1"]);
  const after = memoryNote(events[0], undone);
  assert.match(after, /Taken back/);
  assert.doesNotMatch(after, /data-memory-undo/);
  assert.equal(memoryNote(events[1], undone), "");
});

test("pictures kept with a memory are shown small and open in the viewer", () => {
  const pictured = { ...kept, id: "m7", content: "This is how I arrange the room.", pictures: [{ id: "p1", route: "/api/assistant/memory-pictures/p1" }, { id: "p2", route: "/api/assistant/memory-pictures/p2" }] };
  const note = memoryNote({ kind: "memory", action: "kept", memory: pictured });
  assert.match(note, /Remembered: “This is how I arrange the room\.” The 2 pictures you attached are kept with it\./);
  assert.equal((note.match(/<img /g) || []).length, 2);
  assert.match(note, /href="\/api\/assistant\/memory-pictures\/p1" data-view-file="\/api\/assistant\/memory-pictures\/p1" data-view-name="Remembered picture 1"/);
  assert.match(memoryNote({ kind: "memory", action: "kept", memory: { ...pictured, pictures: pictured.pictures.slice(0, 1) } }), /The picture you attached is kept with it\./);
  assert.doesNotMatch(memoryNote({ kind: "memory", action: "kept", memory: kept }), /<img |picture/);  // a memory of words is as it was
});

test("a replacement names what it replaced, and a course or term fact names its owner", () => {
  const note = memoryNote({ kind: "memory", action: "replaced", memory: { ...kept, id: "m2", content: "Three versions now." },
    replaced: { id: "m1", content: "Make four versions of every quiz." } });
  assert.match(note, /It replaces “Make four versions of every quiz\.”/);
  const scoped = memoryNote({ kind: "memory", action: "kept", memory: { ...kept, scope: "term", applies_to: "MATH 140 Fall 2026" } });
  assert.match(scoped, /Remembered for MATH 140 Fall 2026:/);
  const fragment = memoryNote({ kind: "memory", action: "kept", memory: { ...kept, standalone: false } });
  assert.match(fragment, /may not read well on its own/);
});

test("what is not kept says why, in one line each", () => {
  assert.match(memoryNote({ kind: "memory", action: "student" }), /Not remembered: it is about one student\. Their student notes/);
  assert.match(memoryNote({ kind: "memory", action: "unruled" }), /Not remembered: it mentions a student, and I could not write it as a rule without them\. <a href="#memory\?memory=new">Add it in Memory<\/a>/);
  assert.match(memoryNote({ kind: "memory", action: "record" }), /belongs in its own record/);
  assert.match(memoryNote({ kind: "memory", action: "dated" }), /about one day, so it belongs on your <a href="#calendar">calendar<\/a>/);
  assert.match(memoryNote({ kind: "memory", action: "unplaced", scope: "term" }), /which term this is for/);
  assert.match(memoryNote({ kind: "memory", action: "known", memory: kept }), /Already remembered/);
  for (const action of ["student", "unruled", "record", "dated", "unplaced", "known"]) assert.doesNotMatch(memoryNote({ kind: "memory", action, memory: kept, scope: "course" }), /data-memory-undo/);
});

test("a course Faculty does not have, and a full store, are each said in one line", () => {
  assert.match(memoryNote({ kind: "memory", action: "unplaced", scope: "course", named: "MATH 142" }), /Not remembered: MATH 142 is not one of your courses in Faculty\. Add it in Memory if it should be kept\./);
  assert.match(memoryNote({ kind: "memory", action: "unplaced", scope: "course" }), /I can’t tell which course this is for/);
  const full = memoryNote({ kind: "memory", action: "full", limit: 500 });
  assert.match(full, /Not remembered: Memory holds 500 at most\. Remove one that no longer holds, in <a href="#memory">Memory<\/a>\./);
  assert.doesNotMatch(full, /data-memory-undo/);
});

test("a memory's words are escaped and shortened in the note", () => {
  const note = memoryNote({ kind: "memory", action: "kept", memory: { ...kept, content: `<img src=x onerror=alert(1)> ${"long ".repeat(60)}` } });
  assert.doesNotMatch(note, /<img/);
  assert.match(note, /…”/);
  // A statement's own full stop serves; one is added only when it has none.
  assert.match(memoryNote({ kind: "memory", action: "kept", memory: kept }), /quiz\.” <button/);
  assert.match(memoryNote({ kind: "memory", action: "kept", memory: { ...kept, content: "Use tables" } }), /“Use tables”\. <button/);
});

test("the note stays in the conversation, in the turn of the message that left it", () => {
  const events = [{ kind: "user", seq: 1, text: "Always include a key." }, { kind: "memory", seq: 2, action: "kept", memory: kept },
    { kind: "assistant", seq: 3, text: "Done." }];
  assert.equal(isStep(events[1]), false);
  assert.equal(turnsOf(events).length, 1);
  assert.match(eventMarkup(events[1], { undone: new Set() }), /Remembered:/);
  assert.match(eventMarkup(events[1], { undone: new Set(["m1"]) }), /Taken back/);
});

test("an answer's fold lists the memories its request was given, as they stand now, with Change and Remove", () => {
  const turn = [{ kind: "user", text: "Draft quiz 5.", at: "2026-10-10T10:00:00-05:00" },
    { kind: "memories", given: [{ id: "m1", revision: 1 }, { id: "m2", revision: 2, how: "chosen" }, { id: "m3", revision: 1, how: "picture" }], at: "2026-10-10T10:00:01-05:00" },
    { kind: "assistant", text: "Here it is.", at: "2026-10-10T10:00:09-05:00" },
    { kind: "memories", given: [{ id: "m1", revision: 1 }], at: "2026-10-10T10:00:10-05:00" }];
  assert.deepEqual(memoriesGiven(turn).map((item) => item.id), ["m1", "m2", "m3"]);  // each once, however many requests the turn made
  const memories = {
    m1: { id: "m1", revision: 1, status: "current", content: "Use <b>tables</b>.", applies_to: "Everything", source: "form", created_at: "2026-10-09T12:00:00+00:00" },
    m2: { id: "m2", revision: 3, status: "current", content: "Make three versions of each quiz.", applies_to: "MATH 140", source: "chat", created_at: "2026-10-10T12:00:00+00:00" },
    m3: { id: "m3", revision: 2, status: "removed", reason: "removed", content: "This is how I arrange the room.", applies_to: "Everything", source: "chat", created_at: "2026-10-10T12:00:00+00:00" },
  };
  const html = turnMarkup(turn, false, (event) => eventMarkup(event), { memories });
  assert.match(html, /How it got this<\/span><span class="disclosure-hint">3 memories · 10 s</);
  assert.match(html, /Memories given · 3/);
  assert.doesNotMatch(html, /<details[^>]*open/);  // closed, as the steps are: the conversation stays quiet
  assert.match(html, /“Use &lt;b&gt;tables&lt;\/b&gt;\.”/);
  assert.match(html, /Everything · written in Memory · kept 2026-10-09 · revision 1</);
  assert.match(html, /MATH 140 · from a chat message · kept 2026-10-10 · revision 3 · chosen for this request · changed since this answer</);
  assert.match(html, /data-given-remove="m2" data-revision="3"/);  // Remove names the revision that stands, never the one given
  // Change opens that memory's own row on the Memory page.
  assert.match(html, /<a class="text-button" href="#memory\?memory=m1">Change</);
  assert.match(html, /<a class="text-button" href="#memory\?memory=m2">Change</);
  assert.equal(memoryHref(), "#memory");
  assert.equal((html.match(/>Change</g) || []).length, 2);
  // One removed since reads so, offers Restore, and no longer Change or Remove.
  assert.match(html, /data-memory-given="m3" data-gone="true"/);
  assert.match(html, /its picture looks like the one you attached · removed since</);
  assert.match(html, /data-given-restore="m3">Restore</);
  assert.doesNotMatch(html, /data-given-remove="m3"/);
  // Erased since: nothing of its words is left to show.
  const erased = memoriesGivenMarkup([{ id: "m9", revision: 1 }], { m9: { id: "m9", erased: true } });
  assert.match(erased, /A memory erased since this answer\./);
  assert.doesNotMatch(erased, /button|Change/);
  assert.match(memoriesGivenMarkup([{ id: "m9", revision: 1 }], {}), /erased since/);
  // Taken back and replaced say which; a view from an earlier server gives the count alone.
  assert.match(memoriesGivenMarkup([{ id: "m1", revision: 1 }], { m1: { ...memories.m1, status: "removed", reason: "undone" } }), /taken back since/);
  assert.match(memoriesGivenMarkup([{ id: "m1", revision: 1 }], { m1: { ...memories.m1, status: "superseded", reason: "replaced" } }), /replaced since/);
  assert.equal(memoriesGivenMarkup([{ id: "m1", revision: 1 }], undefined), '<div class="assistant-given" data-memories-given><p class="assistant-given-head">Memories given · 1</p></div>');
  // A turn given none is drawn exactly as before, and the event itself is never a line in the conversation.
  const plain = [{ kind: "user", text: "Hello." }, { kind: "assistant", text: "Hi." }];
  assert.doesNotMatch(turnMarkup(plain, false, (event) => eventMarkup(event)), /How it got this|Memories given/);
  assert.equal(eventMarkup({ kind: "memories", given: [{ id: "m1", revision: 1 }] }), "");
  assert.equal(isStep({ kind: "memories", given: [] }), false);
  assert.doesNotMatch(turnMarkup(turn, true, (event) => eventMarkup(event), { memories }), /Memories given/);  // while it works: the live steps only
});

test("the note of a statement that may not read alone opens that memory's own row on the Memory page", () => {
  const note = memoryNote({ kind: "memory", action: "kept", memory: { id: "m1", content: "Three from now on.", scope: "workspace", applies_to: "Everything", standalone: false } });
  assert.match(note, /<a href="#memory\?memory=m1">reword it</);
  assert.doesNotMatch(note, /data-assistant-tab-open/);
});

test("while the Assistant rewords a statement its note says so, and offers the form only for words left as typed", () => {
  // STILL BEING CONSIDERED (requirement, 2026-10-10).
  const fragment = { kind: "memory", action: "kept", memory: { id: "m1", content: "Three from now on.", scope: "workspace", applies_to: "Everything", standalone: false } };
  const during = memoryNote(fragment, new Set(), new Map(), "m1");
  assert.match(during, /“Three from now on\.” It may not read well on its own; I’m rewording it\. <button class="text-button" type="button" data-memory-undo="m1">Undo</);
  assert.doesNotMatch(during, /<a /);
  assert.match(eventMarkup(fragment, { rewording: "m1" }), /I’m rewording it\./);  // through the chat's own drawing
  // Another memory being reworded, or none: this one was left as typed, and its link opens its form.
  assert.match(memoryNote(fragment, new Set(), new Map(), "m2"), /href="#memory\?memory=m1">reword it</);
  assert.match(eventMarkup(fragment, {}), /href="#memory\?memory=m1">reword it</);
  // One that reads on its own says neither, whatever is being reworded.
  assert.doesNotMatch(memoryNote({ ...fragment, memory: { ...fragment.memory, standalone: true } }, new Set(), new Map(), "m1"), /rewording|reword it/);
});

test("a statement the instructor reworded in Memory shows their words, and is no longer the Assistant's to undo", () => {
  const typed = { id: "m1", content: "Three from now on.", scope: "workspace", applies_to: "Everything", standalone: false, revision: 1, actor: "assistant" };
  const theirs = { ...typed, content: "Make three versions of every quiz.", standalone: true, revision: 2, actor: "instructor", reason: "edited" };
  const events = [{ kind: "memory", action: "kept", memory: typed }, { kind: "memory", action: "reworded", memory: theirs }];
  const note = memoryNote(events[0], new Set(), memoryReworded(events));
  assert.match(note, /Remembered: “Make three versions of every quiz\.” You reworded it in Memory\.<\/p>/);
  assert.doesNotMatch(note, /data-memory-undo|Keep my words|You typed|may not read well/);
  assert.equal(memoryNote(events[1], new Set(), memoryReworded(events)), "");
});

test("a reworded statement shows the words that stand, what was typed, and the way back to them", () => {
  const typed = { id: "m1", content: "Three from now on, not <four>.", scope: "workspace", applies_to: "Everything", standalone: false, revision: 1, pictures: [{ id: "p1", route: "/api/assistant/memory-pictures/p1" }] };
  const now = { ...typed, content: "Make three versions of each quiz from now on, not four.", typed: typed.content, standalone: true, revision: 2, pictures: undefined };
  const events = [{ kind: "memory", action: "kept", memory: typed }, { kind: "memory", action: "reworded", memory: now }];
  // Until it is reworded the note is as it always was: kept as typed, and it says it may not read well alone.
  assert.match(memoryNote(events[0]), /Remembered: “Three from now on, not &lt;four&gt;\.” The picture you attached is kept with it\. It may not read well on its own/);
  const reworded = memoryReworded(events);
  assert.deepEqual([...reworded.keys()], ["m1"]);
  const note = memoryNote(events[0], new Set(), reworded);
  assert.match(note, /Remembered: “Make three versions of each quiz from now on, not four\.” You typed “Three from now on, not &lt;four&gt;\.” <button class="text-button" type="button" data-memory-typed="m1">Keep my words<\/button>/);
  assert.doesNotMatch(note, /may not read well/);
  assert.match(note, /data-memory-undo="m1"/);
  assert.equal((note.match(/<img /g) || []).length, 1);  // the picture kept with it is still shown
  assert.equal(memoryNote(events[1], new Set(), reworded), "");  // the rewording is no line of its own
  // "Keep my words": the note is as typed again, with no second offer.
  const back = [...events, { kind: "memory", action: "as_typed", memory: { id: "m1" } }];
  assert.equal(memoryReworded(back).size, 0);
  const after = memoryNote(back[0], new Set(), memoryReworded(back));
  assert.match(after, /Remembered: “Three from now on, not &lt;four&gt;\.”/);
  assert.doesNotMatch(after, /Keep my words/);
  assert.equal(memoryNote(back[2], new Set(), memoryReworded(back)), "");
  // Undone after rewording: taken back, in the words that stood.
  assert.match(memoryNote(events[0], new Set(["m1"]), reworded), /Taken back: “Make three versions of each quiz from now on, not four\.”/);
  // Through the chat's own drawing, and in the list and the panel, the Assistant's wording is named as such.
  assert.match(eventMarkup(events[0], { reworded }), /Keep my words/);
  const given = memoriesGivenMarkup([{ id: "m1", revision: 2 }], { m1: { ...now, status: "current", source: "chat", created_at: "2026-10-10T12:00:00+00:00" } });
  assert.match(given, /revision 2 · reworded by the Assistant</);
});

test("a rule said of one student's case says it was written without the student, here and in the lists", () => {
  // A RULE SAID OF ONE STUDENT'S CASE (requirement, 2026-10-10).
  const rule = { id: "m9", content: "An absence to attend another course's exam review is not excused.", scope: "term", applies_to: "MATH 140 Fall 2026", standalone: true, revision: 1, ruled: true };
  const note = memoryNote({ kind: "memory", action: "kept", memory: rule });
  assert.match(note, /Remembered for MATH 140 Fall 2026: “An absence to attend another course&#39;s exam review is not excused\.” I wrote this from your message, without the student\. <button class="text-button" type="button" data-memory-undo="m9">Undo<\/button>/);
  assert.doesNotMatch(note, /Keep my words|You typed|may not read well/);  // what was typed named a student and was not kept
  assert.match(memoryNote({ kind: "memory", action: "kept", memory: rule }, new Set(["m9"])), /Taken back/);
  const given = memoriesGivenMarkup([{ id: "m9", revision: 1 }], { m9: { ...rule, status: "current", source: "chat", created_at: "2026-10-10T12:00:00+00:00" } });
  assert.match(given, /revision 1 · written by the Assistant, without the student</);
  // LEARNED FROM AN EDIT: one the Assistant wrote from a correction of a follow-up's card says so, in the list and under an answer.
  const learned = { ...rule, ruled: undefined, learned: true, status: "current", source: "followup", created_at: "2026-10-13T12:00:00+00:00" };
  assert.match(memoriesGivenMarkup([{ id: "m9", revision: 1 }], { m9: learned }), /revision 1 · written by the Assistant from your correction</);
  // A memory in the instructor's own words says neither.
});
