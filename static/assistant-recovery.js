// ASSISTANT: explicit review requests; the server forces every resulting write to an approval card.
// The request names what the instructor saw (the row's title), never a raw route; the page goes as `review_context`.
/** @param {string} [title] */
export function recoveryPrompt(title = 'Workspace health') {
  return `Diagnose why “${String(title || 'this work').trim()}” is blocked and prepare a fix for my review.`;
}
