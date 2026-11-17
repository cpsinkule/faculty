// GRADING: navigation over existing term-owned responses, never a grading decision.
// Only the last location is retained in this browser tab; Later and filters last for this page session.
import {inPrintedOrder} from "./grading-guide.js";

const sessions = new Map(), KEY = "faculty.review-locations.v1", LIMIT = 20;
const keyFor = (plan, scope) => `${plan.id}/${scope.job || "all"}`;

export function reviewSession(plan, scope = {}) {
  const key = keyFor(plan, scope);
  if (!sessions.has(key)) {
    let cursor = null;
    try { cursor = JSON.parse(sessionStorage.getItem(KEY) || "{}")[key] || null; } catch { /* Storage may be unavailable. */ }
    // `review`: the responses a flag review is ruling on, or is about to by itself (2026-10-11); stepped past like `later`.
    sessions.set(key, {cursor, later: new Set(), reason: "", review: new Set()});
    if (sessions.size > LIMIT) sessions.delete(sessions.keys().next().value);
  }
  return sessions.get(key);
}

export function rememberReview(plan, scope, cursor) {
  reviewSession(plan, scope).cursor = cursor;
  try {
    const saved = JSON.parse(sessionStorage.getItem(KEY) || "{}");
    const entries = Object.entries(saved).filter(([key]) => key !== keyFor(plan, scope));
    entries.push([keyFor(plan, scope), cursor]);
    sessionStorage.setItem(KEY, JSON.stringify(Object.fromEntries(entries.slice(-LIMIT))));
  } catch { /* Navigation works without browser storage. */ }
}

export function reviewRows(queue, question = "") {
  return (queue?.questions || []).filter(q => !question || q.question_id === question).flatMap(q =>
    q.groups.flatMap(g => g.members.map(member => ({...member, question: q.question_id, group: g.id}))));
}

// A remembered location is used only if that exact response still waits in that exact question/group.
export function reviewStart(plan, queue, scope = {}, cursor = null) {
  const entries = new Map((queue?.questions || []).map(q => [q.question_id, q]));
  if (cursor && (!scope.question || cursor.question === scope.question)) {
    const group = entries.get(cursor.question)?.groups.find(g => g.id === cursor.group);
    if (group?.members.some(m => m.id === cursor.response)) return cursor;
  }
  for (const question of inPrintedOrder(plan.questions)) {
    if (scope.question && question.question_id !== scope.question) continue;
    const group = entries.get(question.question_id)?.groups.find(g => g.members.length);
    if (group) return {question: question.question_id, group: group.id, response: group.members[0].id};
  }
  return null;
}

export function matchesReason(member, reason) {
  return !reason || (member.reason_kinds || []).includes(reason);
}

export function nextReview(queue, scope, session, preferred = "") {
  const rows = reviewRows(queue, scope.question).filter(row => !session.later.has(row.id) && !session.review?.has(row.id) && matchesReason(row, session.reason));
  return rows.find(row => row.id === preferred) || rows.find(row => row.group === scope.group) || rows[0] || null;
}
