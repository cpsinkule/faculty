// SCHEDULING "ONE CLOCK" (stage 1a): the Calendar page reads clocks by the same rule as the server's scheduling.clock "any" mode.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { clockMinutes } from "../static/calendar-feed.js";

const { cases } = JSON.parse(await readFile(new URL("./fixtures/scheduling/clock_cases.json", import.meta.url), "utf8"));

test("calendar-feed.js clockMinutes reads every case as scheduling.clock \"any\" does", () => {
  for (const item of cases) assert.equal(clockMinutes(item.text), item.any, item.text);
});
