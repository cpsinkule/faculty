// @ts-check
// ASSISTANT MEMORY (requirement, 2026-10-10): what the Assistant kept from a message you typed, said
// in the chat where it happened; the whole list is the Memory page (memory-settings.js, `#memory`). Nothing here decides what
// is kept: the server does, from your own words only (assistant/memory.py). This module draws the note and what an answer was
// given, and names the buttons.
import { esc } from "./assistant-markdown.js";
import { viewLink } from "./file-viewer.js";

/** A memory's own place on the Memory page ("reword it", Change, "Add it in Memory"): its row, opened for its words. */
export const memoryHref = (/** @type {string} */ id = "") => `#memory${id ? `?memory=${encodeURIComponent(id)}` : ""}`;

const short = (/** @type {string} */ words, limit = 140) => { const flat = String(words || "").replace(/\s+/g, " ").trim(); return flat.length > limit ? `${flat.slice(0, limit - 1).trimEnd()}…` : flat; };
// A quoted statement as a sentence of its own: its own full stop serves, so the note never ends “….”.
const quoted = (/** @type {string} */ words) => { const said = short(words); return `“${esc(said)}”${/[.!?…]$/.test(said) ? "" : "."}`; };

/**
 * The pictures kept with a memory (requirement, 2026-10-10), small; each opens in the file viewer.
 * @param {any} memory
 */
function keptPictures(memory) {
  const kept = memory.pictures || [];
  return kept.length ? `<span class="assistant-memory-pictures">${kept.map((/** @type {any} */ picture, /** @type {number} */ index) => `<a ${viewLink(picture.route, `Remembered picture ${index + 1}`, esc)}><img src="${esc(picture.route)}" alt="A picture kept with this memory" loading="lazy" width="72"></a>`).join("")}</span>` : "";
}

/** The memories a later event took back, so their note stops offering Undo. @param {any[]} events */
export function memoryUndone(events) {
  return new Set((events || []).filter((event) => event.kind === "memory" && event.action === "undone").map((event) => event.memory?.id));
}

/**
 * THE REWRITE STEP: the memories whose words the Assistant reworded so they read on their own, as they stand in this chat
 * ({id: the memory with its new words and `typed`}); one whose own words were put back ("Keep my words") is left out.
 * One the instructor reworded in Memory is here too, with their words, `actor` "instructor" and no `typed`.
 * @param {any[]} events
 */
export function memoryReworded(events) {
  /** @type {Map<string, any>} */
  const reworded = new Map();
  for (const event of events || []) {
    if (event.kind !== "memory" || !event.memory?.id) continue;
    if (event.action === "reworded") reworded.set(event.memory.id, event.memory);
    else if (event.action === "as_typed") reworded.delete(event.memory.id);
  }
  return reworded;
}

/**
 * The line a kept (or declined) statement leaves in the conversation. `undone` is `memoryUndone(events)`; `reworded` is
 * `memoryReworded(events)`; `rewording` is the memory the Assistant is rewording now (the conversation view's
 * `remembering.rewording`), whose note says so instead of offering the form.
 * @param {any} event @param {Set<string>} [undone] @param {Map<string, any>} [reworded] @param {string} [rewording]
 */
export function memoryNote(event, undone = new Set(), reworded = new Map(), rewording = "") {
  const now = reworded.get(event.memory?.id);
  // Reworded since: the note shows the words that stand, what was typed, and the way back to them.
  const memory = now && (event.action === "kept" || event.action === "replaced") ? { ...event.memory, ...now, pictures: event.memory.pictures } : event.memory || {};
  const line = (/** @type {string} */ body, tone = "") => `<p class="assistant-memory"${tone ? ` data-tone="${tone}"` : ""} data-memory-note="${esc(event.action)}">${body}</p>`;
  if (event.action === "kept" || event.action === "replaced") {
    const where = memory.scope === "workspace" ? "" : ` for ${esc(memory.applies_to)}`;
    const replaced = event.replaced ? ` It replaces ${quoted(event.replaced.content)}` : "";
    // STILL BEING CONSIDERED: while the Assistant rewords it the note says so; "reword it" is for words it left as typed,
    // and opens this memory's own form.
    const reword = memory.standalone !== false ? "" : rewording && rewording === memory.id ? " It may not read well on its own; I’m rewording it."
      : ` It may not read well on its own; <a href="${esc(memoryHref(memory.id))}">reword it</a>.`;
    if (undone.has(memory.id)) return line(`Taken back: ${quoted(memory.content)}${event.replaced ? " The earlier one stands again." : ""}`);
    const count = (memory.pictures || []).length;
    const pictured = count ? ` ${count === 1 ? "The picture you attached is" : `The ${count} pictures you attached are`} kept with it.` : "";
    const typed = now?.typed ? ` You typed ${quoted(now.typed)} <button class="text-button" type="button" data-memory-typed="${esc(memory.id)}">Keep my words</button>` : "";
    // A RULE SAID OF ONE STUDENT'S CASE: these are the Assistant's words for your rule, and what you typed was not kept.
    const ruled = memory.ruled ? " I wrote this from your message, without the student." : "";
    // Reworded by the instructor in Memory: the words are theirs now, so the Assistant's keeping is no longer there to undo.
    const own = now?.actor === "instructor";
    const undo = own ? " You reworded it in Memory." : ` <button class="text-button" type="button" data-memory-undo="${esc(memory.id)}">Undo</button>`;
    return line(`Remembered${where}: ${quoted(memory.content)}${ruled}${typed}${pictured}${replaced}${reword}${undo}${keptPictures(memory)}`, "kept");
  }
  if (event.action === "known") return line(`Already remembered: ${quoted(memory.content)}`);
  if (event.action === "student") return line("Not remembered: it is about one student. Their student notes are the place for that.");
  if (event.action === "unruled") return line('Not remembered: it mentions a student, and I could not write it as a rule without them. <a href="#memory?memory=new">Add it in Memory</a> if it should be kept.');
  if (event.action === "record") return line("Not remembered: a date, time, room or grade belongs in its own record, where it stays current.");
  if (event.action === "dated") return line('Not remembered: it is about one day, so it belongs on your <a href="#calendar">calendar</a>, where it ends with the day. A timed event titled “Away” or “Unavailable” keeps makeups out of the office hours it covers.');
  if (event.action === "unplaced" && event.named) return line(`Not remembered: ${esc(event.named)} is not one of your courses in Faculty. Add it in Memory if it should be kept.`);
  if (event.action === "unplaced") return line(`Not remembered: I can’t tell which ${event.scope === "course" ? "course" : "term"} this is for. Say it from that course’s page, or add it in Memory.`);
  if (event.action === "full") return line(`Not remembered: Memory holds ${esc(event.limit)} at most. Remove one that no longer holds, in <a href="#memory">Memory</a>.`);
  return "";  // "undone", "reworded" and "as_typed" change the note of the memory they are about, and are no lines of their own
}

const day = (/** @type {string} */ stamp) => String(stamp || "").slice(0, 10);

// MEMORIES GIVEN (requirement, 2026-10-10): under an answer, the memories
// its request was told, as they stand now, each with Change and Remove. Faculty knows what a request was given, never which
// of it shaped the answer, so the words are "given", not "used". The events name memories by reference; the conversation's
// view carries how each stands (`memories`), so one removed or erased since reads so.
const GIVEN_SOURCE = { chat: "from a chat message", form: "written in Memory", followup: "from a follow-up", recording: "said in a recording" };
const GIVEN_HOW = { always: "with every request", chosen: "chosen for this request", nearest: "nearest to this request, not judged", picture: "its picture looks like the one you attached" };
const GONE = { superseded: "replaced since", removed: "removed since" };

/** The memories a turn's requests were given, each once, in the order told. @param {any[]} turn */
export function memoriesGiven(turn) {
  /** @type {Map<string, any>} */
  const given = new Map();
  for (const event of turn || []) if (event.kind === "memories") for (const item of event.given || []) if (item?.id && !given.has(item.id)) given.set(item.id, item);
  return [...given.values()];
}

/**
 * "Memories given" inside an answer's "How it got this". `memories` is the conversation view's map of how each stands now;
 * without it (an earlier server) only the count is said.
 * @param {any[]} given `memoriesGiven(turn)` @param {Record<string, any> | undefined} memories
 */
export function memoriesGivenMarkup(given, memories) {
  if (!given.length) return "";
  const head = `<p class="assistant-given-head">Memories given · ${given.length}</p>`;
  if (!memories) return `<div class="assistant-given" data-memories-given>${head}</div>`;
  const rows = given.map((/** @type {any} */ item) => {
    const memory = memories[item.id];
    if (!memory || memory.erased) return `<li data-memory-given="${esc(item.id)}"><span class="assistant-card-note">A memory erased since this answer.</span></li>`;
    const stands = memory.status === "current";
    const since = stands ? (memory.revision !== item.revision ? "changed since this answer" : "") : (memory.reason === "undone" ? "taken back since" : GONE[memory.status] || "removed since");
    const facts = [memory.applies_to, GIVEN_SOURCE[memory.source] || "", `kept ${day(memory.created_at)}`, `revision ${memory.revision}`,
      memory.typed ? "reworded by the Assistant" : "", memory.ruled ? "written by the Assistant, without the student" : "", memory.learned ? "written by the Assistant from your correction" : "", GIVEN_HOW[item.how] || "", since].filter(Boolean);
    const actions = stands
      ? `<a class="text-button" href="${esc(memoryHref(memory.id))}">Change</a><button class="text-button danger" type="button" data-given-remove="${esc(memory.id)}" data-revision="${memory.revision}">Remove</button>`
      : `<button class="text-button" type="button" data-given-restore="${esc(memory.id)}">Restore</button>`;
    return `<li data-memory-given="${esc(memory.id)}"${stands ? "" : ' data-gone="true"'}><span class="assistant-given-words">${quoted(memory.content)}</span><span class="assistant-card-note">${esc(facts.join(" · "))}</span><span class="assistant-given-actions">${actions}</span></li>`;
  }).join("");
  return `<div class="assistant-given" data-memories-given>${head}<ul class="assistant-given-list">${rows}</ul></div>`;
}
