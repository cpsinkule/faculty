import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { bandModel } from "../static/grading-certification-stats.js";

// Wave 2 integration checks (2026-10-02): where two modules say the same thing, they say it with the same words.

const source = (name) => readFile(new URL(`../static/${name}`, import.meta.url), "utf8");

test("the shell's first paint and each page's own header give the same subtitle", async () => {
  const app = await source("app.js");
  const shell = (route) => app.match(new RegExp(`^  ${route}: \\(\\) => \\(\\{ title: "[^"]+", subtitle: "([^"]+)"`, "m"))?.[1];
  const page = (text, title) => text.match(new RegExp(`title: "${title}", subtitle: "([^"]+)"`))?.[1];
  const exams = page(await source("exams.js"), "Exams"), documents = page(await source("documents.js"), "Assignments");
  assert.equal(exams, "Each assignment’s paper, seating, notices, collection and grading in one place.");
  assert.equal(shell("exams"), exams);
  assert.equal(documents, "Create assignments and quizzes, and print their stamped copies.");
  assert.equal(shell("documents"), documents);
});

test("with nobody graded, the certification band names the step the header's primary leads to", () => {
  const view = { certification: { state: "none" }, checks: [], counts: { participants: 339, missing: 339, complete: 0, in_progress: 0 }, students: [] };
  // Waiting for its papers (an approved quiz not given yet): the header's primary is Upload scans, so the band says the same.
  const waiting = { key: "grade", label: "Grade", waiting: true, title: "Upload the scans once it is given", action: { label: "Upload scans", href: "#inbox?upload=1" } };
  assert.equal(bandModel(view, "", { next: waiting }).body, "Certifying now would record 0 for all 339 students. Next: upload the scans once it is given.");
  const inbox = { ...waiting, title: "Finish the scan in the Inbox" };
  assert.match(bandModel(view, "", { next: inbox }).body, / Next: finish the scan in the Inbox\.$/);
  // Papers in grading: the step itself.
  assert.match(bandModel(view, "", { next: { key: "grade", label: "Grade" } }).body, / Next: Grade\.$/);
  assert.equal(bandModel(view, "", {}).body, "Certifying now would record 0 for all 339 students.");
});
