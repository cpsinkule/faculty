// @ts-check
/* JEV SEND-CHECKS (docs/decisions/jev.md; requirement, 2026-10-03): the notes shown beside a message's
   review, from POST /api/mail/check or /api/canvas/messages/check. Advice only: Send never waits for them, and a failed check
   says so quietly. No inline styles (CSP). */

/**
 * @param {any} result null while checking; {error: true} when the check could not run
 * @param {(value: unknown) => string} e
 */
export function checksMarkup(result, e) {
  if (!result) return '<p class="field-hint">Checking the draft…</p>';
  if (result.error) return '<p class="field-hint">Couldn’t check this draft. Send works as usual.</p>';
  const notes = Array.isArray(result.notes) ? result.notes : [];
  const detail = result.status !== "checked" && result.detail ? `<p class="field-hint">${e(result.detail)}</p>` : "";
  if (!notes.length) return result.status === "checked" ? '<p class="field-hint">Checks found nothing to point out.</p>' : detail;
  const tone = notes.some((/** @type {any} */ note) => note.tone === "warning") ? "warning" : "info";
  return `<div class="callout" data-tone="${tone}" role="note"><p><strong>Before you send</strong></p>
    <ul>${notes.map((/** @type {any} */ note) => `<li>${e(note.text)}</li>`).join("")}</ul>${detail}</div>`;
}

/**
 * Fill `host` with the checks of one review; a host no longer on the page is left alone.
 * @param {Element | null} host @param {() => Promise<any>} request @param {(value: unknown) => string} e
 */
export async function fillChecks(host, request, e) {
  if (!host) return;
  host.innerHTML = checksMarkup(null, e);
  let result;
  try {
    result = await request();
  } catch {
    result = { error: true };
  }
  if (host.isConnected) host.innerHTML = checksMarkup(result, e);
}
