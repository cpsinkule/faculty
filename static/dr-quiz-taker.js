// ACCOMMODATIONS "DR QUIZ TAKERS" (2026-10-15): the one checkbox of the Ctrl-K student card and the Accommodations page, one stored value.
import { CAMPUS_ZONE } from "./calendar-feed.js";

const DAY = new Intl.DateTimeFormat("en-US", { timeZone: CAMPUS_ZONE, month: "short", day: "numeric" });
export const takerLine = state => !state?.set_at ? ""
  : `${state.on ? "Marked" : "Unmarked"} by ${state.set_by === "assistant" ? "the Assistant" : "you"} on ${DAY.format(new Date(state.set_at))}`;
// Offered only to a student with an accommodation or a DR booking (2026-10-16); one already marked keeps the box, so it can be unticked.
/** @param {string} enrollmentId @param {any} state @param {(value: any) => string} escape @param {boolean} [offered] */
export const takerMarkup = (enrollmentId, state, escape, offered = true) => !offered && !state?.on ? "" : `<label class="checkbox-field"><input type="checkbox" data-dr-quiz-taker="${escape(enrollmentId)}"${state?.on ? " checked" : ""}>DR quiz taker</label>${takerLine(state) ? `<span class="field-hint">${escape(takerLine(state))}</span>` : ""}${offered ? "" : '<span class="field-hint">No accommodations on record.</span>'}`;
/** Sets (never toggles) the flag with a fresh request ID. @param {Function} api @param {string} enrollmentId @param {boolean} on */
export const saveTaker = (api, enrollmentId, on) => api(`/api/accommodations/dr-quiz-takers/${encodeURIComponent(enrollmentId)}`, "POST", { on, request_id: crypto.randomUUID() });
