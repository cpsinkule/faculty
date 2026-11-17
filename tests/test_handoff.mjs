// The in-memory hand-off between pages (static/handoff.js): the gradebook's "See the graded paper" gives a row to one grading
// plan's Certification and Export page, which takes it once as it mounts; any other page's take drops it, and so does a minute
// without one, so it never lingers.
import assert from "node:assert/strict";
import { give, take } from "../static/handoff.js";

assert.equal(take("plan-1"), null);
give({ plan: "plan-1", row: "section-a:enrollment-b" });
assert.deepEqual(take("plan-1"), { plan: "plan-1", row: "section-a:enrollment-b" });
assert.equal(take("plan-1"), null, "taken once");
give({ plan: "plan-1", row: "section-a:enrollment-b" });
assert.equal(take("plan-2"), null, "another plan's page opens nobody");
assert.equal(take("plan-1"), null, "and the hand-off is gone");
give({ plan: "17", row: "s:e" });
assert.deepEqual(take(17), { plan: "17", row: "s:e" }, "a numeric plan ID matches its string");
give({ plan: "plan-1", row: "s:e" }, 1000);
assert.deepEqual(take("plan-1", 61000), { plan: "plan-1", row: "s:e" }, "taken within the minute");
give({ plan: "plan-1", row: "s:e" }, 1000);
assert.equal(take("plan-1", 61001), null, "a hand-off older than a minute opens nobody");
assert.equal(take("plan-1", 61002), null, "and is gone");
console.log("handoff ok");
