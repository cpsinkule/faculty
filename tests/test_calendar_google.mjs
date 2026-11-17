import assert from "node:assert/strict";
import test from "node:test";

import { displayTime, TYPE_FILTERS } from "../static/calendar-feed.js";
import { ago, syncBadge } from "../static/calendar-google.js";

// The Calendar's Google Calendar sync as the page states it: the toolbar's one word, how long ago, and Google's all-day events.
test("the toolbar names the sync's state, and hides it where Google is not set up", () => {
  assert.equal(syncBadge(null), null);
  assert.equal(syncBadge({ configured: false }), null);
  assert.deepEqual(syncBadge({ configured: true, permission: false }), { label: "Connect", tone: "idle" });
  assert.deepEqual(syncBadge({ configured: true, permission: true, enabled: false }), { label: "Off", tone: "idle" });
  assert.deepEqual(syncBadge({ configured: true, permission: true, enabled: true, read_at: null }), { label: "Starting", tone: "ok" });
  assert.deepEqual(syncBadge({ configured: true, permission: true, enabled: true, read_at: "2026-09-28T10:00:00Z" }), { label: "Synced", tone: "ok" });
  assert.deepEqual(syncBadge({ configured: true, permission: true, enabled: true, error: "Google is busy" }), { label: "Needs attention", tone: "danger" });
});

test("a sync time reads as how long ago", () => {
  const now = Date.parse("2026-09-28T12:00:00Z");
  assert.equal(ago("2026-09-28T11:59:40Z", now), "just now");
  assert.equal(ago("2026-09-28T11:57:00Z", now), "3 minutes ago");
  assert.equal(ago("2026-09-28T10:00:00Z", now), "2 hours ago");
  assert.equal(ago(null, now), "");
});

test("Google events are a type of their own, and an all-day one reads All day", () => {
  assert.equal(TYPE_FILTERS.google, "Google Calendar");
  assert.equal(displayTime({ source: "google", all_day: true, time: "" }), "All day");
  assert.equal(displayTime({ source: "google", all_day: false, time: "15:30", end_time: "16:30" }), "3:30–4:30 PM");
});
