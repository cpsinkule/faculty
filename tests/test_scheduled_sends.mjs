import assert from "node:assert/strict";
import test from "node:test";

// MAIL-SEND-LATER: the Send later… field and the list of scheduled messages (static/scheduled-sends.js), pure parts only.
const later = await import(new URL("../static/scheduled-sends.js", import.meta.url).href);
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

test("the field starts tomorrow at 8:00 AM on the campus calendar", () => {
  // 2026-11-10 03:30 UTC is still Nov 9 in Texas (9:30 PM CST): tomorrow is Nov 10.
  assert.equal(later.campusDefault(new Date("2026-11-10T03:30:00Z")), "2026-11-10T08:00");
  assert.equal(later.campusDefault(new Date("2026-12-31T18:00:00Z")), "2027-01-01T08:00");
  assert.equal(later.campusDefault(new Date("2026-11-09T15:00:00Z"), 0, 17), "2026-11-09T17:00");
});

test("a send time is said in campus time, a wall-clock value as written", () => {
  assert.equal(later.campusText("2026-11-10T14:00:00+00:00"), "Tue, Nov 10, 8:00 AM");
  assert.equal(later.sendAtText("2026-11-10T08:00"), "Tue, Nov 10, 8:00 AM");
  assert.equal(later.sendAtText("2026-07-10T13:00:00Z"), "Fri, Jul 10, 8:00 AM");
  assert.equal(later.sendAtText("next Monday"), "");
  assert.equal(later.sendAtText(undefined), "");
});

test("each state has its chip and its time", () => {
  const waiting = { state: "waiting", send_at: "2026-11-10T14:00:00+00:00" };
  assert.deepEqual(later.stateChip(waiting), ["neutral", "Scheduled"]);
  assert.equal(later.whenLine(waiting), "Sends Tue, Nov 10, 8:00 AM");
  assert.deepEqual(later.stateChip({ state: "held" }), ["warning", "Not sent"]);
  const sent = { state: "sent", send_at: "2026-11-10T14:00:00+00:00", result: { status: "uncertain", sent_at: "2026-11-10T15:35:00+00:00", late_minutes: 95 } };
  assert.deepEqual(later.stateChip(sent), ["warning", "Unconfirmed"]);
  assert.equal(later.whenLine(sent), "Sent Tue, Nov 10, 9:35 AM (95 min late)");
  assert.equal(later.whenLine({ state: "cancelled", send_at: "2026-11-10T14:00:00+00:00" }), "Was due Tue, Nov 10, 8:00 AM");
});

test("the list offers Send now and Cancel until it goes, Dismiss for a problem, and escapes the words", () => {
  assert.equal(later.scheduledMarkup([], escape), "");
  const html = later.scheduledMarkup([
    { id: "a", state: "waiting", channel: "mail", subject: "Exam <2>", audience: "87 on Bcc", send_at: "2026-11-10T14:00:00+00:00", body: "Bring a pencil.", attachments: [] },
    { id: "b", state: "held", channel: "canvas", subject: "Room", audience: "Section 507", send_at: "2026-11-10T14:00:00+00:00", body: "", detail: "Faculty could not send it.", actor: "assistant" },
    { id: "c", state: "sent", channel: "mail", subject: "Done", audience: "a@x", send_at: "2026-11-10T14:00:00+00:00", body: "", problem: true,
      result: { status: "failed", sent_at: "2026-11-10T14:00:30+00:00", detail: "Invalid To header" } },
  ], escape);
  assert.match(html, /Exam &#60;2&#62;/);
  assert.equal((html.match(/data-later-send=/g) || []).length, 2);
  assert.equal((html.match(/data-later-cancel=/g) || []).length, 2);
  assert.equal((html.match(/data-later-dismiss="c"/g) || []).length, 1);
  assert.match(html, /Canvas message · to Section 507 · Was due Tue, Nov 10, 8:00 AM · scheduled by the Assistant/);
  assert.match(html, /Faculty could not send it\./);
  assert.match(html, /Invalid To header/);
  assert.doesNotMatch(later.scheduledMarkup([{ id: "a", state: "waiting", channel: "canvas", subject: "s", audience: "Section 507", send_at: "2026-11-10T14:00:00+00:00", body: "" }], escape, { channel: false }), /Canvas message/);
});
