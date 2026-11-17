// @ts-check
// One page handing a student to another page in memory (rest-people, 2026-10-02): the gradebook's "See the graded paper" goes to
// the assignment's Certification and Export page, which opens that student's drawer. A student is never in the address
// (FRONTEND.md, Certification and Export), and nothing is stored: the hand-off lives in this tab's memory until the page it
// names takes it as it mounts, and any other `take` drops it, so a later visit to that page opens nobody. A hand-off not taken
// within a minute (the page never mounted: its plan could not be read, or the instructor went elsewhere first) is dropped too.

const FRESH_MS = 60000;

/** @type {{plan: string, row: string, at: number} | null} */
let pending = null;

/** Hand `row` (a `section_id:enrollment_id` row key) to the next page of grading plan `plan`.
 * @param {{plan: string, row: string}} value @param {number} [now] */
export const give = ({ plan, row }, now = Date.now()) => { pending = { plan: String(plan), row: String(row), at: now }; };

/** What was handed to `plan`'s page in the last minute, once: a later call returns null.
 * @param {string | number} plan @param {number} [now] @returns {{plan: string, row: string} | null} */
export const take = (plan, now = Date.now()) => {
  const value = pending && pending.plan === String(plan) && now - pending.at <= FRESH_MS ? { plan: pending.plan, row: pending.row } : null;
  pending = null;
  return value;
};
