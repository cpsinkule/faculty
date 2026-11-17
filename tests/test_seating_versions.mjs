// Run: node tests/test_seating_versions.mjs
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
const source = await readFile(new URL("../static/seating-versions.js", import.meta.url), "utf8");
const { savedExamVersionList } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

function chart() {
  return { room: { review_required: [] }, check: { ok: true }, exam_versions: {
    count: 3, ready: true, total: 3, unassigned: [], conflicts: [],
    counts: [{ version: "A", count: 1 }, { version: "B", count: 1 }, { version: "C", count: 1 }],
    rows: [{ label: "Row A", students: [
      { seat: "A1", name: "Student One", version: "C" },
      { seat: "A3", name: "Student Two", version: "A" },
      { seat: "A8", name: "Student Three", version: "B" },
    ] }],
  } };
}

test("printing uses the exact saved manual letters without cycling again or changing the chart", () => {
  const current = chart(), before = structuredClone(current);
  const result = savedExamVersionList(current);
  assert.equal(result, current.exam_versions);
  assert.deepEqual(result.rows[0].students.map((student) => student.version), ["C", "A", "B"]);
  assert.deepEqual(current, before);
});
test("explicit manual conflicts remain printable with their warning data", () => {
  const current = chart();
  current.exam_versions.conflicts = [{ row: "Row A", seats: ["A1", "A3"], version: "A" }];
  assert.deepEqual(savedExamVersionList(current).conflicts, current.exam_versions.conflicts);
});
test("a newly occupied seat without a saved letter cannot produce an incomplete list", () => {
  const current = chart();
  current.exam_versions.unassigned = ["A5"];
  current.exam_versions.ready = false;
  assert.throws(() => savedExamVersionList(current), /Version needed at A5/);
});
test("charts without versions, seats, or a valid roster and room require repair before printing", () => {
  assert.throws(() => savedExamVersionList({}), /Assign exam versions/);
  const current = chart();
  current.exam_versions.count = null;
  assert.throws(() => savedExamVersionList(current), /Assign exam versions/);
  for (const mutate of [
    (c) => { c.room.review_required = ["Verify seat labels"]; },
    (c) => { c.check.ok = false; },
    (c) => { c.exam_versions.total = 0; },
    (c) => { c.exam_versions.ready = false; },
  ]) {
    const c = chart(); mutate(c);
    assert.throws(() => savedExamVersionList(c));
  }
});
