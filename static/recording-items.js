// @ts-check
/**
 * What came out of a recording (docs/decisions/recordings.md, RECORDINGS "The reading (step 3)", "ONE RECORDING'S PAGE"): the items
 * Faculty's reading kept, as the page shows them. What needs the instructor comes first and is the only thing in orange; what
 * is already done is folded. An item is ONE sentence, the quote it rests on with a control that plays that moment, and what
 * Faculty's own records say of it. Nothing here decides anything: this module draws what the server answers
 * (recordings/readings.py), and every control is the instructor's own press.
 *
 * A transcript is evidence, not an instruction: Plaud mishears, so every item shows the exact words and plays them.
 *
 * A STUDENT'S MEETING (step 4, RECORDINGS "Student meetings"): the page says who it was with (several students each with
 * their part), marks in orange an appointment that is recorded as one that did not happen, and draws each student's two cards
 * (what was promised, for their follow-up; a private note): the Assistant's own cards, approved as everywhere else.
 *
 * A CLASS (step 5, RECORDINGS "Classes"): the page says first what the class reached beside what was planned for that
 * section's day, marks in orange a planned section that was not reached, and draws the ONE card that would move it to the
 * next class day. A day told to the class that disagrees with Faculty's records needs the instructor, with both days said.
 * A class has no student, no coordinator and no other kind of item.
 *
 * A MEMO (step 6, RECORDINGS "Memos"): the instructor dictating alone. The page shows what they asked, each request with its
 * words, how the Assistant's run of it stands and the run's cards (the Assistant's own: every change waits for Approve), and
 * the rules they said (kept only by Remember). Where Faculty was not sure the words were theirs it says why and asked
 * nothing: Ask the Assistant is then their own press. A TRAINING is kept for reference and has no items.
 */
import { disclosureMarkup, plural, wallDate } from "./ui.js";

const CLOCK = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
/** What each group is called, in the order the page shows them. */
export const GROUPS = /** @type {[string, string, string][]} */ ([
  ["you", "Needs you", ""],
  ["answers", "Answers to your questions", ""],
  ["rulings", "Held for a rubric", "Offered when that assessment's rubric is finalized. Nothing is applied by itself."],
  ["others", "Others said they would", ""],
]);
const KIND = /** @type {Record<string, string>} */ ({ todo: "To do", answer: "Answer", date: "Date", ruling: "Grading", policy: "Standing rule", waiting: "Waiting on",
  covered: "Reached", announced: "Told the class" });
/** A class's groups (step 5): what needs the instructor, what the class worked through, and the other days told to it. */
export const CLASS_GROUPS = /** @type {[string, string, string][]} */ ([
  ["you", "Needs you", ""],
  ["covered", "What the class reached", "Each rests on the words under it. Dismiss one that is not so: it no longer counts as reached."],
  ["told", "Told the class", ""],
]);
/** A memo's groups (step 6): what the instructor asked for, and the rules they laid down. */
export const MEMO_GROUPS = /** @type {[string, string, string][]} */ ([
  ["asked", "You asked", "The Assistant works on each request in the background. Every change it wants waits for your approval."],
  ["rules", "Rules you said", "Kept only by your own Remember."],
]);
const MEMO_KIND = /** @type {Record<string, string>} */ ({ todo: "Request", policy: "Standing rule" });
const WORKING = ["queued", "running"];
/** In a student's meeting the other side is the student. */
const STUDENT_TITLES = /** @type {Record<string, string>} */ ({ answers: "They told you", others: "They said they would" });
const STUDENT_KIND = /** @type {Record<string, string>} */ ({ todo: "You said you would", answer: "They said", date: "A day that was set", waiting: "They said they would" });
const FOR = /** @type {Record<string, string>} */ ({ coordinator: "the coordinator", others: "others" });

/** "0:24" of milliseconds into the recording. @param {number} ms */
function offset(ms) {
  const seconds = Math.max(0, Math.floor(Number(ms || 0) / 1000));
  const hours = Math.floor(seconds / 3600), minutes = Math.floor((seconds % 3600) / 60), rest = String(seconds % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

/** The controls an item offers where it stands. @param {any} item @param {(value: unknown) => string} e */
export function controlsMarkup(item, e) {
  const button = (/** @type {string} */ state, /** @type {string} */ label) => `<button type="button" class="text-button" data-item-state="${state}" data-recording-item="${e(item.id)}">${label}</button>`;
  if (item.group === "done") {
    // What Faculty's records show as done was never settled by the instructor: there is nothing of theirs to open again.
    return item.state !== "open" || item.ruled ? button("reopened", "Open again") : button("dismissed", "Dismiss");
  }
  if (item.group === "rulings" || item.group === "covered") return button("dismissed", "Dismiss");
  if (item.group === "asked") {
    // A memo's request: put to the Assistant by the instructor's own press where Faculty did not; asked again the same way.
    const state = item.ask?.state;
    const ask = state === "unsent" ? `<button type="button" class="button" data-item-ask="${e(item.id)}">Ask the Assistant</button>`
      : WORKING.includes(state) ? "" : `<button type="button" class="text-button" data-item-ask-again="${e(item.id)}">Ask again…</button>`;
    return `${ask}${button("done", "Done")}${button("dismissed", "Dismiss")}`;
  }
  // What a student told the instructor goes on their records: one that was not so is dismissed, and is then on none of them.
  if (item.group === "answers") return `${button("done", "Done")}${item.enrollment_id ? button("dismissed", "Dismiss") : ""}`;
  const remember = item.kind === "policy" && (item.group === "you" || item.group === "rules") ? `<button type="button" class="button" data-item-remember="${e(item.id)}">Remember…</button>` : "";
  const propose = item.group === "you" && item.found?.offer === "calendar" && !item.card?.pending
    ? `<button type="button" class="button" data-item-card="${e(item.id)}">Propose a calendar entry</button>` : "";
  return `${remember}${propose}${button("done", "Done")}${button("dismissed", "Dismiss")}`;
}

/** What Faculty's own records say of an item. Orange never: the heading says what needs the instructor. @param {any} found @param {(value: unknown) => string} e */
export function foundMarkup(found, e) {
  if (!found) return "";
  return `<p class="recording-item-found" data-state="${e(found.state)}">${e(found.text)}${found.href ? ` <a class="link-arrow" href="${e(found.href)}">Open</a>` : ""}</p>`;
}

/** What became of an item, said under it. @param {any} item @param {(value: unknown) => string} e */
export function becameMarkup(item, e) {
  if (item.followup && item.state === "open") return `<p class="recording-item-found" data-state="done">On their follow-up. <a class="link-arrow" href="#follow-ups">Follow-up</a></p>`;
  if (item.memory) return `<p class="recording-item-found" data-state="done">Kept as a memory. <a class="link-arrow" href="#memory">Memory</a></p>`;
  if (item.ruled) return `<p class="recording-item-found" data-state="done">${item.ruled === "accepted" ? "Accepted" : "Declined"} at the rubric's finalization.</p>`;
  if (item.state === "done") return `<p class="recording-item-found" data-state="done">You marked it done.</p>`;
  if (item.state === "dismissed") return `<p class="recording-item-found" data-state="done">You dismissed it.</p>`;
  const card = item.card;
  if (card && !card.pending && item.group !== "done") {
    const said = card.status === "declined" ? "You declined the calendar entry." : card.status === "expired" ? "Its calendar card lapsed after a day." : "";
    return said ? `<p class="recording-item-found" data-state="info">${e(said)}</p>` : "";
  }
  return "";
}

/** How a memo's request stands with the Assistant, as one line and its tone. @param {any} ask @returns {{text: string, tone: string}} */
export function askLine(ask) {
  const waiting = (ask?.cards || []).filter((/** @type {any} */ card) => card.pending).length;
  switch (ask?.state) {
    case "unsent": return { text: ask.why || "Not put to the Assistant yet.", tone: "warning" };
    // Queued with the Assistant off: nothing will run it until the instructor names a model for it, so the line says so.
    case "queued": return ask.why ? { text: ask.why, tone: "warning" } : { text: "Waiting for the Assistant.", tone: "neutral" };
    case "running": return ask.why ? { text: ask.why, tone: "warning" } : { text: "The Assistant is working on it.", tone: "neutral" };
    case "ready": return { text: waiting ? `The Assistant answered. ${plural(waiting, "change")} ${waiting === 1 ? "waits" : "wait"} for your approval.` : "The Assistant answered.", tone: "neutral" };
    case "needs_input": return { text: "The Assistant asked you something. Answer with Ask again.", tone: "warning" };
    case "failed": return { text: "The Assistant could not finish this.", tone: "warning" };
    case "interrupted": return { text: "The Assistant was stopped before it finished. Ask again to have it done.", tone: "warning" };
    case "stale": return { text: "Withdrawn.", tone: "neutral" };
    default: return { text: "", tone: "neutral" };
  }
}

/**
 * A memo's request with the Assistant: where its run stands, the Assistant's answer as plain text (never markup), what it
 * asked back, the run's waiting cards drawn as the Assistant's own, and what became of the others in a few words.
 * @param {any} item @param {(value: unknown) => string} e @param {{card?: (action: any, extra?: string) => string}} [draw]
 */
export function askMarkup(item, e, draw = {}) {
  const ask = item.ask;
  if (!ask || item.group === "done") return "";
  const line = askLine(ask), cards = ask.cards || [];
  const said = ask.answer && !WORKING.includes(ask.state) && ask.state !== "unsent" ? String(ask.answer) : "";
  const answer = !said ? "" : said.length > 700
    ? disclosureMarkup({ label: "The Assistant's answer", hint: "", key: `recording-ask-${item.id}`, className: "recording-fold", body: `<div class="recording-ask-answer">${e(said)}</div>` })
    : `<div class="recording-ask-answer">${e(said)}</div>`;
  const asked = (ask.question?.questions || []).map((/** @type {any} */ question) => `<li>${e(question.title)}</li>`).join("");
  const waiting = draw.card ? cards.filter((/** @type {any} */ card) => card.pending).map((/** @type {any} */ card) => `<div class="recording-item-card">${draw.card?.(card)}</div>`).join("") : "";
  const counted = /** @type {Record<string, number>} */ ({});
  for (const card of cards) if (!card.pending) counted[card.status] = (counted[card.status] || 0) + 1;
  const named = /** @type {Record<string, string>} */ ({ succeeded: "approved and done", declined: "declined", expired: "withdrawn or lapsed", failed: "failed", approved: "approved, not finished" });
  const settled = Object.keys(counted).map((status) => `${counted[status]} ${named[status] || status}`).join(" · ");
  return `<div class="recording-ask" data-ask-state="${e(ask.state)}">
    <p class="recording-capture"${line.tone === "warning" ? ' data-tone="warning"' : ""}>${e(line.text)}</p>
    ${answer}${asked ? `<ul class="recording-card-lines">${asked}</ul>` : ""}${waiting}
    ${settled ? `<p class="recording-item-found" data-state="info">Its changes: ${e(settled)}.</p>` : ""}</div>`;
}

/**
 * One item: its sentence, when it is about, the quote with the control that plays it, what Faculty found, and its controls.
 * @param {any} item @param {(value: unknown) => string} e @param {{card?: (action: any, extra?: string) => string, whose?: (item: any) => string, memo?: boolean}} [draw]
 */
export function itemMarkup(item, e, draw = {}) {
  const about = item.kind === "ruling" || item.about?.what === "rubric" || item.about?.what === "assessment"
    ? [item.assessment, item.about?.question ? `question ${item.about.question}` : ""].filter(Boolean).join(" · ") : "";
  const whose = item.group === "others" && FOR[item.for] && !item.enrollment_id ? `For ${FOR[item.for]}` : "";   // in a student's meeting the heading says whose
  const kind = (item.enrollment_id && STUDENT_KIND[item.kind]) || (draw.memo && MEMO_KIND[item.kind]) || KIND[item.kind] || "";
  const notes = item.kind === "covered" ? (item.about?.notes || []).join(", ") : "";
  const when = !item.when ? "" : item.kind === "date" && !item.happening ? `Not on ${item.when}` : item.kind === "announced" && !item.happening ? `No class ${item.when}` : item.when;
  const tags = [kind, draw.whose?.(item) || "", about, notes, when, whose].filter(Boolean);
  const card = item.card?.pending && draw.card ? `<div class="recording-item-card">${draw.card(item.card)}</div>` : "";
  return `<li class="recording-item" data-patch-key="item-${e(item.id)}" data-item-row="${e(item.id)}" data-group="${e(item.group)}">
    <p class="recording-item-sentence">${e(item.sentence)}</p>
    <p class="recording-item-tags">${tags.map((tag) => `<span>${e(tag)}</span>`).join("")}</p>
    <p class="recording-item-quote"><button type="button" class="recording-line-time" data-play="${e(item.start_ms)}" aria-label="Play from ${e(offset(item.start_ms))}">${e(offset(item.start_ms))}</button>
      <span class="recording-line-speaker">${e(item.speaker)}</span><q>${e(item.quote)}</q></p>
    ${foundMarkup(item.found, e)}${askMarkup(item, e, draw)}${becameMarkup(item, e)}${card}
    <div class="recording-controls">${controlsMarkup(item, e)}</div></li>`;
}

/** Where the reading stands, as one line, and whether it asks something of the instructor. `memo` (a memo's `{sure, why}`):
 * whose voice it is, in place of which speaker Faculty took as the instructor; not sure, the line says why nothing was put to
 * the Assistant, in orange (the requests wait on the instructor's press).
 * @param {any} reading @param {any} [memo] @returns {{text: string, tone: string}} */
export function readingLine(reading, memo = null) {
  const state = reading?.state;
  if (state === "read") {
    const when = reading.read_at ? CLOCK.format(new Date(reading.read_at)) : "";
    // "Reading it again" only while a Read again waits for the worker. A transcript Plaud changed is read by itself: until it
    // is, the line says so, and when it cannot be it says why (never a claim that it is under way).
    const again = reading.again ? " Faculty is reading it again now."
      : reading.detail ? ` Reading it again did not work: ${reading.detail}`
        : reading.changed ? " Plaud has changed the transcript since: Faculty reads it again in its next round." : "";
    const you = memo ? (memo.sure ? "One voice, taken as yours." : memo.why)
      : reading.you ? `It took “${reading.you}” as you.` : "Faculty could not tell which speaker is you: name yourself among the speakers above, then Read again.";
    return { text: `Read by Faculty${when ? ` ${when}` : ""}. ${you}${again}`, tone: (memo ? memo.sure : reading.you) ? "neutral" : "warning" };
  }
  if (state === "waiting") {
    return reading.detail ? { text: reading.detail, tone: "warning" }
      : { text: "Faculty is reading this recording. What it left you with shows here in a minute or two.", tone: "neutral" };
  }
  if (state === "failed") return { text: `Faculty could not read this recording. ${reading.detail || ""}`.trim(), tone: "warning" };
  if (state === "not_read") return { text: reading.detail || "", tone: "neutral" };
  return { text: "", tone: "neutral" };
}

/** What the quote check left out: said plainly, folded. @param {any[]} dropped @param {(value: unknown) => string} e */
export function droppedMarkup(dropped, e) {
  if (!(dropped || []).length) return "";
  return disclosureMarkup({ label: "Left out by the quote check", hint: String(dropped.length), key: "recording-dropped", className: "recording-fold",
    body: `<p class="recording-facts">The reading said these, and Faculty did not find their words in the transcript where it said they were. They are not items.</p>
      <ul class="recording-list">${dropped.map((/** @type {any} */ item) => `<li class="recording-item"><p class="recording-item-sentence">${e(item.sentence || "(no sentence)")}</p>
        <p class="recording-facts">${e(item.reason)}${item.quote ? `: “${e(item.quote)}”` : ""}</p></li>`).join("")}</ul>` });
}

/** "0:00 to 0:28": a student's part of a recording. @param {{start_ms: number, end_ms: number}} part */
export function partText(part) {
  return `${offset(part.start_ms)} to ${offset(part.end_ms)}`;
}

/** What one of a student's two cards would do, in plain lines, shown on the card. @param {string} kind @param {any} action @param {(value: unknown) => string} e */
export function cardLines(kind, action, e) {
  const body = action?.body || {};
  if (kind === "note") return `<div class="recording-card-text">${e(body.text || "")}</div>`;
  const fresh = (body.commitments || []).filter((/** @type {any} */ commitment) => commitment.recording_item_id);
  // Who owes the next step, then the sentence; a day that was said is the day Faculty brings it back to the instructor.
  const who = (/** @type {any} */ commitment) => (commitment.waiting_on === "you" ? "Yours" : commitment.waiting_on === "student" ? "Theirs" : "A day");
  return `<ul class="recording-card-lines">${fresh.map((/** @type {any} */ commitment) => `<li><span class="recording-count">${who(commitment)}</span> ${e(commitment.title)}${
    commitment.check_back ? ` <span class="recording-count">Check back ${e(wallDate(commitment.check_back))}</span>` : ""}</li>`).join("")}</ul>`;
}

/**
 * Who a student's meeting was with: each student, their part where the recording holds several, an appointment recorded as
 * one that did not happen (orange: it needs the instructor, and Faculty changes neither), what their follow-up already
 * holds, and their two cards. @param {any} data @param {(value: unknown) => string} e
 * @param {{card?: (action: any, extra?: string) => string}} [draw]
 */
export function studentsMarkup(data, e, draw = {}) {
  const students = data?.students || [];
  if (data?.meeting !== "student" || !students.length) return "";
  const rows = students.map((/** @type {any} */ student) => {
    const facts = [(student.sections || []).length ? `Section ${student.sections.join(", ")}` : "", student.part ? partText(student.part) : ""].filter(Boolean).join(" · ");
    const off = student.off ? `<p class="recording-capture" data-tone="warning" data-student-off>${e(student.off)}</p>` : "";
    const tracked = student.on_followup ? `<p class="recording-item-found" data-state="done">${plural(student.on_followup, "thing")} from this meeting ${
      student.on_followup === 1 ? "is" : "are"} on their follow-up. <a class="link-arrow" href="#follow-ups">Follow-up</a></p>` : "";
    const noted = student.cards?.note?.status === "succeeded" ? `<p class="recording-item-found" data-state="done">A note of this meeting is in your notes about them.</p>` : "";
    const cards = ["commitments", "note"].map((kind) => (student.cards?.[kind]?.pending && draw.card
      ? `<div class="recording-item-card">${draw.card(student.cards[kind], cardLines(kind, student.cards[kind], e))}</div>` : "")).join("");
    const offer = (/** @type {string} */ kind, /** @type {string} */ label) => (student.offer?.[kind]
      ? `<button type="button" class="button" data-student-card="${kind}" data-student="${e(student.enrollment_id)}">${label}</button>` : "");
    return `<div class="recording-student" data-patch-key="student-${e(student.enrollment_id)}" data-student-row="${e(student.enrollment_id)}">
      <h3 class="recording-head">${e(student.name)}${facts ? ` <span class="recording-count">${e(facts)}</span>` : ""}</h3>
      ${off}${tracked}${noted}${cards}
      <div class="recording-controls">${offer("commitments", "Propose what was promised")}${offer("note", "Propose a note")}</div></div>`;
  }).join("");
  return `<section class="recording-students" data-recording-students-list>${rows}</section>`;
}

/** What a move card would write, in plain lines: this section's own topics for each day it changes, exactly as the card
 * holds them. @param {any} day @param {any} action @param {(value: unknown) => string} e */
export function moveLines(day, action, e) {
  const topics = action?.body?.topics || {};
  const rows = Object.keys(topics).sort().map((date) => {
    const own = topics[date]?.sections?.[day.section_id];
    if (!own) return "";
    return `<li><span class="recording-count">${e(wallDate(date))}</span> ${e([(own.notes || []).join(", "), own.note].filter(Boolean).join(" · ") || "no topics")}</li>`;
  }).join("");
  return rows ? `<p class="recording-facts">Section ${e(day.section)}'s topics would read:</p><ul class="recording-card-lines">${rows}</ul>` : "";
}

/**
 * A class's day (step 5): what the class reached beside what was planned for that section's day; in orange a planned
 * section that was not reached, with the ONE card that would move it to the next class day (the Assistant's own card: the
 * plan never changes by itself), or the control that proposes it again. What was reached beyond the plan, or on an earlier
 * recorded day, is said plainly and asks nothing. @param {any} data @param {(value: unknown) => string} e
 * @param {{card?: (action: any, extra?: string) => string}} [draw]
 */
export function classDayMarkup(data, e, draw = {}) {
  const day = data?.class_day;
  if (data?.meeting !== "class" || !day) return "";
  const named = (/** @type {any[]} */ list) => (list || []).map((entry) => `${entry.number}${entry.title ? ` ${entry.title}` : ""}`).join(" · ");
  const planned = [named(day.planned), day.note].filter(Boolean).join(" · ") || "No topics planned for this day";
  const reached = named(day.reached) || (day.state === "unread" ? "Not read yet" : "No notes section heard");
  const missing = named(day.not_reached);
  const mark = day.state === "differs" ? `<p class="recording-capture" data-tone="warning" data-class-differs>Not reached: ${e(missing)}.</p>`
    : day.state === "left" ? `<p class="recording-item-found" data-state="info">Not reached: ${e(missing)}. You declined the move, so the plan stands.</p>`
      : day.state === "as_planned" ? `<p class="recording-item-found" data-state="done">${e(day.text)}</p>`
        : day.state === "none" ? `<p class="recording-item-found" data-state="info">${e(day.text)}</p>` : "";
  const ahead = (day.ahead || []).length && (day.planned || []).length ? `<p class="recording-item-found" data-state="info">Also reached, not planned for this day: ${e(named(day.ahead))}.</p>` : "";
  const earlier = (day.earlier || []).map((/** @type {any} */ entry) => `<p class="recording-item-found" data-state="info">${e(named([entry]))} was reached on ${e(entry.day)}.</p>`).join("");
  // Why no move can be proposed (no later class day; the next one would hold too many topics), in the server's words.
  const stuck = day.blocked ? `<p class="recording-item-found" data-state="info" data-class-blocked>${e(day.blocked)}</p>` : "";
  const card = day.card?.pending && draw.card ? `<div class="recording-item-card">${draw.card(day.card, moveLines(day, day.card, e))}</div>` : "";
  const offer = day.offer ? `<div class="recording-controls"><button type="button" class="button" data-class-move>Propose the move to ${e(day.next)}</button></div>` : "";
  return `<section class="recording-class" data-recording-class data-state="${e(day.state)}">
    <h3 class="recording-head">Section ${e(day.section)} · ${e(day.day)}${day.href ? ` <a class="link-arrow" href="${e(day.href)}">Class schedule</a>` : ""}</h3>
    <dl class="recording-class-rows"><dt>Planned</dt><dd>${e(planned)}${day.own ? ' <span class="chip" data-tone="neutral">This section</span>' : ""}</dd><dt>Reached</dt><dd>${e(reached)}</dd></dl>
    ${mark}${ahead}${earlier}${stuck}${card}${offer}</section>`;
}

/**
 * What came out of a recording: the line saying where its reading stands, then what needs the instructor, the answers to their
 * questions, the rulings held for a rubric, what others owe, and (folded) what is already done and what the quote check left out.
 * @param {any} data the recording as `GET /api/recordings/<id>` answers it
 * @param {(value: unknown) => string} e @param {{card?: (action: any, extra?: string) => string, whose?: (item: any) => string}} [draw]
 */
export function outcomeMarkup(data, e, draw = {}) {
  const reading = data?.reading, student = data?.meeting === "student", lesson = data?.meeting === "class";
  // Whose meeting it is, and the control that sets the students and their parts: there before anything is read.
  const parts = student ? `<button type="button" class="text-button" data-recording-students>Students and parts…</button>` : "";
  const students = studentsMarkup(data, e, draw) || classDayMarkup(data, e, draw);
  if (!reading || reading.state === "none") return student && students ? `<div class="recording-reading"><div class="recording-controls">${parts}</div></div>${students}` : students;
  const dictated = data?.meeting === "memo";
  if (dictated) draw = { ...draw, memo: true };
  const line = readingLine(reading, dictated ? data.memo : null), items = data.items || [], read = reading.state === "read";
  if (student && (data.students || []).length > 1) {
    const names = new Map((data.students || []).map((/** @type {any} */ entry) => [entry.enrollment_id, entry.name]));
    draw = { ...draw, whose: (/** @type {any} */ item) => names.get(item.enrollment_id) || (item.enrollment_id ? "" : "In nobody's part") };
  }
  const again = reading.readable && (read || reading.state === "failed" || (reading.state === "waiting" && reading.detail))
    ? `<button type="button" class="text-button" data-recording-read${reading.again ? " disabled" : ""}>Read again</button>` : "";
  const who = data.coordinator?.term_id && reading.state !== "not_read" && !student && !lesson && !dictated
    ? `<button type="button" class="text-button" data-recording-coordinator>${data.coordinator.name ? `Coordinator: ${e(data.coordinator.name)}` : "Name the coordinator"}</button>` : "";
  const status = line.text || again || who || parts ? `<div class="recording-reading"><p class="recording-capture"${line.tone === "warning" ? ' data-tone="warning"' : ""}>${e(line.text)}</p>
    <div class="recording-controls">${parts}${who}${again}</div></div>` : "";
  const groups = (lesson ? CLASS_GROUPS : dictated ? MEMO_GROUPS : GROUPS).map(([key, title, hint]) => {
    const found = items.filter((/** @type {any} */ item) => item.group === key);
    if (!found.length) return "";
    // A memo's groups say in orange how many of theirs need the instructor (a request the Assistant is not working on, a rule
    // still open); the count beside it is all of them.
    const mine = dictated ? found.filter((/** @type {any} */ item) => item.yours).length : 0;
    return `<section class="recording-group" data-items="${key}"><h3 class="recording-head">${e((student && STUDENT_TITLES[key]) || title)} <span class="${key === "you" ? "chip" : "recording-count"}"${key === "you" ? ' data-tone="warning"' : ""}>${found.length}</span>${
      mine ? ` <span class="chip" data-tone="warning">${mine} for you</span>` : ""}</h3>
      ${hint ? `<p class="recording-facts">${e(hint)}</p>` : ""}<ul class="recording-list">${found.map((/** @type {any} */ item) => itemMarkup(item, e, draw)).join("")}</ul></section>`;
  }).join("");
  const done = items.filter((/** @type {any} */ item) => item.group === "done");
  const folded = done.length ? disclosureMarkup({ label: "Already done", hint: String(done.length), key: "recording-done", className: "recording-fold",
    body: `<ul class="recording-list">${done.map((/** @type {any} */ item) => itemMarkup(item, e, draw)).join("")}</ul>` }) : "";
  const nothing = !read || items.length ? "" : dictated ? `<p class="recording-facts">This memo asked for nothing: the reading found no request and no rule in it.</p>`
    : lesson ? `<p class="recording-facts">The reading found no day told to the class and no notes section worked through.</p>`
    : `<p class="recording-facts">This meeting left nothing for you: the reading found no task, date, answer or rule in it.</p>`;
  return `${status}${students}${nothing}${groups}${folded}${read ? droppedMarkup(reading.dropped, e) : ""}`;
}

/** The transcript fold's label: "Transcript · 646 lines". @param {any} data */
export function transcriptLabel(data) {
  const lines = data?.transcript?.lines || 0;
  return lines ? `Transcript · ${plural(lines, "line")}` : "Transcript";
}

/** Whether the page should ask again by itself: only while a reading is being made, or (a memo) while the Assistant has a
 * request of it queued or under way. @param {any} data */
export function stillReading(data) {
  const reading = data?.reading;
  if (!reading) return false;
  // ... and only while the Assistant is on: a request queued with no model named waits on the instructor, not on a refresh.
  if (data.memo?.assistant !== false && (data.items || []).some((/** @type {any} */ item) => item.group === "asked" && WORKING.includes(item.ask?.state))) return true;
  if (reading.state === "read") return Boolean(reading.again || (reading.changed && !reading.detail));
  return reading.state === "waiting" && !reading.detail && Boolean(reading.readable);
}
