import assert from "node:assert/strict";
import test from "node:test";

import { STANDARD_SCALE, isStandardScale, scaleProblems, scaleSummary, scaleUppers } from "../static/grading-scheme.js";
import { DEFAULT_SCALE, PRESETS, draftSetup, problems } from "../static/gradebook-setup.js";

// GRADEBOOK GRADING SCHEME (requirement, 2026-10-06): the term's letter scale, written the way Canvas writes a grading scheme.
// These are display and pre-save checks only; the server decides every letter.
const entries = list => list.map(([letter, min]) => ({ letter, min }));
const canvasLike = entries([["A", "90"], ["B", "80"], ["C", "67"], ["D", "57"], ["F", "0"]]);

test("a term starts with the standard scheme, which the setup page and its preset share", () => {
  assert.deepEqual(STANDARD_SCALE, [["A", "90"], ["B", "80"], ["C", "70"], ["D", "60"], ["F", "0"]]);
  assert.equal(DEFAULT_SCALE, STANDARD_SCALE);
  assert.equal(PRESETS.plain.scale, STANDARD_SCALE);
  assert.equal(PRESETS.plain.label, "Standard A–F");
  assert.deepEqual(draftSetup({}).scale, entries(STANDARD_SCALE));
});

test("the standard scheme is recognised by its letters and minimums, whatever their spelling", () => {
  assert.equal(isStandardScale(entries(STANDARD_SCALE)), true);
  assert.equal(isStandardScale(entries([["A", "90.00"], ["B", "80"], ["C", "70"], ["D", "60"], ["F", "0"]])), true);
  assert.equal(isStandardScale(canvasLike), false);
  assert.equal(isStandardScale(entries([["A", "90"], ["B", "80"], ["C", "70"], ["D", "60"]])), false);
  assert.equal(isStandardScale([]), false);
});

test("each letter's range runs from below the letter above it, as Canvas writes it", () => {
  assert.deepEqual(scaleUppers(canvasLike), ["100%", "< 90%", "< 80%", "< 67%", "< 57%"]);
  assert.deepEqual(scaleUppers(entries([["A", "92.5"], ["B", ""], ["F", "0"]])), ["100%", "< 92.5%", "< …"]);
  assert.deepEqual(scaleUppers([]), []);
});

test("the course Home's line names every minimum and the floor", () => {
  assert.equal(scaleSummary(canvasLike), "A 90% · B 80% · C 67% · D 57% · F below 57%");
  assert.equal(scaleSummary(entries(STANDARD_SCALE)), "Standard · A 90% · B 80% · C 70% · D 60% · F below 60%");
  assert.equal(scaleSummary(entries([["P", "0"]])), "P for every percentage");
  assert.equal(scaleSummary([]), "");
});

test("obvious mistakes are found before a save, each naming its field", () => {
  assert.deepEqual(scaleProblems(canvasLike), []);
  assert.deepEqual(scaleProblems([]).map(problem => problem.message), ["The grading scheme needs at least one letter."]);
  assert.deepEqual(scaleProblems(entries([["A", "80"], ["a", "90"], ["", "x"], ["ABCDE", "-1"]])).map(problem => problem.focus),
    ["scale.letter:1", "scale.min:1", "scale.letter:2", "scale.min:2", "scale.letter:3", "scale.min:3"]);
  assert.deepEqual(scaleProblems(entries([["A", "90"], ["B", "1000.5"]])).map(problem => problem.focus), ["scale.min:1"]);
  assert.deepEqual(scaleProblems(entries([["A", "90.12345"]])).map(problem => problem.focus), ["scale.min:0"], "four decimals at most");
  // The setup page's check reports the same scale problems as the dialog.
  const setup = draftSetup({ scale: entries([["A", "80"], ["B", "90"]]) });
  assert.deepEqual(problems(setup), scaleProblems(setup.scale));
});
