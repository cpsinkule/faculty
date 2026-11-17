// @ts-check
/**
 * Messages sent later (docs/decisions/mail.md MAIL-SEND-LATER, 2026-11-07): the Send later… field of the Mail and Canvas reviews,
 * and the list of messages scheduled from them (Mail's Scheduled tab lists every one; the Canvas page its own) with Send now,
 * Cancel and, for a send that failed or could not be confirmed, Dismiss. Times are campus wall-clock times, as the server reads them.
 */
import { disclosureMarkup, errorMessage, setBusy } from "./ui.js";

export const CAMPUS = "America/Chicago";  // scheduling/clock.py ZONE_NAME
const BASE = "/api/scheduled-sends";

/** "2026-11-10T08:00": the campus date `days` from `now` at `hour`, the field's first value. Pure. @param {Date} [now] */
export function campusDefault(now = new Date(), days = 1, hour = 8) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: CAMPUS, year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(now).map((part) => [part.type, part.value]));
  const day = new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day) + days));
  return `${day.toISOString().slice(0, 10)}T${String(hour).padStart(2, "0")}:00`;
}

/** "Tue, Nov 10, 8:00 AM", campus time. Pure. @param {string} iso */
export function campusText(iso) {
  return new Intl.DateTimeFormat("en-US", { timeZone: CAMPUS, weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    .format(new Date(iso));
}

/** A `send_at` as the server reads it ("2026-11-10T08:00" is a campus wall-clock time, shown as written), or "" when it is none.
 * Pure. @param {unknown} value */
export function sendAtText(value) {
  const text = String(value ?? "");
  const wall = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(text);
  if (wall) {
    const [, y, mo, d, h, mi] = wall.map(Number);
    return new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
      .format(new Date(Date.UTC(y, mo - 1, d, h, mi)));
  }
  return Number.isFinite(Date.parse(text)) ? campusText(text) : "";
}

/** The review's Send later… field, hidden until Send later… is chosen. @param {(v: any) => string} e @param {string} [value] */
export function laterFieldMarkup(e, value = campusDefault()) {
  return `<div class="send-later" data-send-later hidden><label class="field">Send on<input type="datetime-local" name="send_at" value="${e(value)}" step="60" />
    <span class="field-hint">Campus time. If Faculty can't send it then (signed out, or restarting), it keeps trying for two hours, then waits for you on Home.</span></label></div>`;
}

const RESULT = /** @type {Record<string, [string, string]>} */ ({
  accepted: ["success", "Sent"], failed: ["danger", "Not sent"], uncertain: ["warning", "Unconfirmed"], mixed: ["warning", "Partly sent"], sending: ["neutral", "Sending…"],
});
const STATE = /** @type {Record<string, [string, string]>} */ ({
  waiting: ["neutral", "Scheduled"], sending: ["neutral", "Sending…"], held: ["warning", "Not sent"], cancelled: ["neutral", "Cancelled"],
});

/** [tone, words] of a scheduled message's chip. Pure. @param {any} item */
export function stateChip(item) {
  if (item.state === "sent") return RESULT[item.result?.status] || ["neutral", "Sent"];
  return STATE[item.state] || ["neutral", item.state];
}

/** When it goes or went, in words. Pure. @param {any} item */
export function whenLine(item) {
  if (item.state === "sent" && item.result) {
    const late = item.result.late_minutes ? ` (${item.result.late_minutes} min late)` : "";
    return `Sent ${campusText(item.result.sent_at)}${late}`;
  }
  if (item.state === "waiting" || item.state === "sending") return `Sends ${campusText(item.send_at)}`;
  return `Was due ${campusText(item.send_at)}`;
}

/**
 * The scheduled messages, soonest first, then those that went or were cancelled. Empty when there are none. Pure.
 * @param {any[]} items @param {(v: any) => string} e @param {{title?: string, channel?: boolean}} [options]
 */
export function scheduledMarkup(items, e, { title = "Scheduled messages", channel = true } = {}) {
  if (!items.length) return "";
  const rows = items.map((item) => {
    const [tone, label] = stateChip(item);
    const kind = item.channel === "canvas" ? "Canvas message" : "Email";
    const place = [channel ? kind : "", `to ${item.audience}`, whenLine(item), item.actor === "assistant" ? "scheduled by the Assistant" : ""].filter(Boolean).join(" · ");
    const why = item.state === "held" ? item.detail : item.waiting ? `Waiting: ${item.waiting}` : item.state === "sent" ? item.result?.detail : "";
    const open = item.state === "waiting" || item.state === "held";
    const actions = `${open ? `<button type="button" class="button" data-later-send="${e(item.id)}">Send now</button><button type="button" class="text-button danger" data-later-cancel="${e(item.id)}">Cancel</button>` : ""}${item.problem ? `<button type="button" class="text-button" data-later-dismiss="${e(item.id)}">Dismiss</button>` : ""}`;
    const files = item.attachments?.length ? `<p class="field-hint">Attached: ${e(item.attachments.map((/** @type {any} */ f) => f.name).join(", "))}</p>` : "";
    return `<li data-patch-key="later-${e(item.id)}" data-state="${e(item.state)}"><span class="scheduled-main"><strong>${e(item.subject)}</strong><small>${e(place)}</small>
      ${why ? `<small class="scheduled-why">${e(why)}</small>` : ""}${disclosureMarkup({ label: "The message", body: `<pre class="mail-text">${e(item.body)}</pre>${files}` })}</span>
      <span class="chip" data-tone="${tone}">${e(label)}</span>${actions ? `<span class="scheduled-actions">${actions}</span>` : ""}</li>`;
  }).join("");
  return `<section class="card scheduled-sends" aria-label="${e(title)}"><header class="scheduled-title"><h2>${e(title)}</h2></header><ul class="scheduled-list">${rows}</ul></section>`;
}

/**
 * Send now, Cancel and Dismiss on a list scheduledMarkup drew: true when `event` was one of them. Send now and Cancel ask first,
 * in the app's dialog; `onChange` hears of every change.
 * @param {Event} event
 * @param {{api: any, toast: any, showDialog: any, dialogHeader: any, escape: (v: any) => string, items: () => any[], onChange: () => void}} deps
 */
export function handleScheduledClick(event, deps) {
  const target = event.target instanceof Element ? event.target.closest("[data-later-send], [data-later-cancel], [data-later-dismiss]") : null;
  if (!(target instanceof HTMLButtonElement)) return false;
  const { api, toast, showDialog, dialogHeader, escape: e, items, onChange } = deps;
  const id = target.dataset.laterSend || target.dataset.laterCancel || target.dataset.laterDismiss || "";
  const item = items().find((other) => other.id === id);
  if (!item) return true;
  const run = async (/** @type {HTMLButtonElement} */ button, /** @type {string} */ action, /** @type {any} */ body, /** @type {string} */ done) => {
    setBusy(button, true);
    try {
      const found = await api(`${BASE}/${encodeURIComponent(id)}/${action}`, "POST", body);
      const dialog = /** @type {HTMLDialogElement} */ (document.getElementById("dialog"));
      if (dialog.open && dialog.querySelector(`[data-later-confirm="${CSS.escape(id)}"]`)) dialog.close();
      const [, label] = stateChip(found);
      toast(action === "send" ? `${item.subject}: ${label}.` : done);
    } catch (cause) {
      toast(errorMessage(cause), true);
    } finally {
      setBusy(button, false);
      onChange();
    }
  };
  if (target.dataset.laterDismiss) {
    void run(target, "dismiss", {}, "Dismissed. Nothing is sent again.");
    return true;
  }
  const sending = Boolean(target.dataset.laterSend);
  const words = sending
    ? { title: "Send it now?", body: `Faculty sends “${item.subject}” to ${item.audience} now, exactly as you approved it.`, go: "Send now" }
    : { title: "Cancel this message?", body: `“${item.subject}” will not be sent. To send it later, write it again.`, go: "Cancel the message" };
  showDialog(`<div data-later-confirm="${e(id)}">${dialogHeader(words.title, "")}<div class="dialog-body"><p>${e(words.body)}</p></div>
    <footer class="dialog-footer"><button type="button" class="button" data-action="close">${sending ? "Not now" : "Keep it"}</button>
    <button type="button" class="button ${sending ? "primary" : "danger"}" data-later-go>${e(words.go)}</button></footer></div>`);
  const go = /** @type {HTMLButtonElement | null} */ (document.querySelector(`[data-later-confirm="${CSS.escape(id)}"] [data-later-go]`));
  if (go) go.onclick = () => void run(go, sending ? "send" : "cancel", sending ? { confirmation: "send" } : {}, "Cancelled. It will not be sent.");
  return true;
}
