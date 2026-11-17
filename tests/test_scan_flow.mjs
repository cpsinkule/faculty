// scan-flow.js: a scan's flow graph, its card and its next step (UX overhaul 2026-09-26, W2-INBOX). Pure markup of the server's
// overview; nothing here decides a state.
import assert from "node:assert/strict";
import test from "node:test";

import { scanCard, scanFlowNav, scanNext, scanNextStep } from "../static/scan-flow.js";
import { escape } from "../static/ui.js";

const stage = (key, label, title, state, status, extra = {}) => ({ key, label, title, page: key === "scan" ? "pages" : key, state, status,
  urgent: status === "Needs you", detail: `${label} detail <b>`, progress: null, href: null, ...extra });
const stages = [
  stage("scan", "Scan", "Check the scanned pages", "done", "Done"),
  stage("assignment", "Assignment", "Choose the assignment", "done", "Done"),
  stage("students", "Students", "Find whose paper each is", "started", "Needs you"),
  stage("papers", "Papers", "Group the pages into papers", "idle", "Not started"),
  stage("review", "Review", "Review the scan", "idle", "Not started"),
];

test("scanNext keeps its keys and adds the stage's goal and where it is", () => {
  const next = scanNext("job-1", stages);
  assert.equal(next.label, "Students");  // unchanged meaning for other readers (system status notices)
  assert.equal(next.href, "#inbox/job/job-1/students");
  assert.equal(next.goal, "Find whose paper each is");
  // One vocabulary for scan states (2026-10-18): Needs you, Working, Failed, Done, Not started.
  assert.equal(next.where, "Needs you · Students");
  assert.equal(next.status, "Needs you: find whose paper each is");
  // A status from before the rename reads the same.
  assert.equal(scanNext("job-1", stages.map((item) => item.key === "students" ? { ...item, status: "To do" } : item)).where, "Needs you · Students");
  const working = scanNext("job-1", stages.map((item) => item.key === "students" ? { ...item, status: "Working", urgent: false } : item));
  assert.deepEqual([working.goal, working.where], ["View progress", "Working · Students"]);
  const idle = scanNext("job-1", stages.map((item) => ({ ...item, state: "done", status: "Done", urgent: false })));
  assert.deepEqual([idle.goal, idle.where, idle.href], ["Open scan", "Nothing here needs you", "#inbox/job/job-1"]);
  const failed = scanNext("job-1", [stage("scan", "Scan", "Check the scanned pages", "failed", "Failed")]);
  assert.deepEqual([failed.goal, failed.where, failed.urgent], ["Check the scanned pages", "Failed · Scan", true]);
});

test("the card's one button says the goal in the stage's own words, with its menu in the header", () => {
  const html = scanCard({ jobId: "job-1", overview: { headline: "Quiz <2>", facts: ["4 pages"], state: "todo", stages }, escape,
    menu: '<div class="action-menu">menu</div>' });
  assert.match(html, /<a class="button primary" data-scan-next-action href="#inbox\/job\/job-1\/students">Find whose paper each is →<\/a>/);
  assert.doesNotMatch(html, /scan-next-button/);
  assert.match(html, /<div class="scan-card-corner"><span class="grading-card-completion">2 of 5 stages done<\/span><div class="action-menu">menu<\/div><\/div><\/header>/);
  assert.match(html, /data-scan-next-status data-urgent="true">Needs you · Students</);
  assert.match(html, /Quiz &lt;2&gt;/);
  assert.doesNotMatch(html, /data-size="compact"/);
  assert.match(scanCard({ jobId: "j", overview: { headline: "h", state: "done", stages }, escape, compact: true }), /<div data-size="compact"><div class="grading-pipeline-scroll"/);
});

test("the next-step banner is the shared next-step callout, only away from the current stage", () => {
  const html = scanNextStep("job-1", stages, "scan", escape);
  assert.match(html, /^<aside class="next-step" data-scan-next-step aria-label="Next step">/);
  assert.match(html, /<p class="eyebrow">Next step · Students<\/p><strong class="next-step-title">Find whose paper each is<\/strong>/);
  assert.match(html, /Students detail &lt;b&gt;/);
  assert.match(html, /<a class="button primary" href="#inbox\/job\/job-1\/students">Students →<\/a>/);
  assert.equal(scanNextStep("job-1", stages, "students", escape), "");
  const working = stages.map((item) => item.key === "students" ? { ...item, status: "Working", urgent: false } : item);
  assert.equal(scanNextStep("job-1", working, "scan", escape), "");
});

test("the graph on a stage page says where the page sits in the flow", () => {
  const html = scanFlowNav("job-1", stages, "papers", escape);
  assert.match(html, /<p class="scan-flow-position" data-scan-flow-position>Stage 4 of 5 · <strong>Papers<\/strong> · Not started<\/p><\/nav>$/);
  assert.doesNotMatch(scanFlowNav("job-1", stages, "sheets", escape), /scan-flow-position/);
});

test("an arrival without a PDF is its Scan stage alone, and its next step says why", () => {
  const lone = [{ ...stage("scan", "Scan", "Check the scanned pages", "done", "Done"), detail: "Nothing to inspect: this arrival holds no PDF." }];
  const next = scanNext("job-2", lone);
  assert.deepEqual([next.goal, next.detail, next.href], ["Open scan", "Nothing to inspect: this arrival holds no PDF.", "#inbox/job/job-2"]);
  // Every other finished scan keeps the general wording.
  const done = scanNext("job-1", stages.map((item) => ({ ...item, state: "done", status: "Done", urgent: false })));
  assert.equal(done.detail, "Every stage of this scan is done or waiting on an earlier one.");
  // Its card draws one cube.
  assert.equal((scanCard({ jobId: "job-2", overview: { headline: "No scanned pages in this arrival", facts: [], state: "done", stages: lone }, escape }).match(/data-pipeline-step=/g) || []).length, 1);
});

test("in a list only a card that needs the instructor is ivory; the scan's own page keeps its one primary", () => {
  // A finished scan whose papers are in grading: 'Open grading' is quiet on a list card (heading) and the primary on its page.
  const graded = [...stages.map((item) => ({ ...item, state: "done", status: "Done", urgent: false })),
    stage("grading", "Grading", "Grade the papers", "done", "Done", { href: "#grading/plan-1", urgent: false })];
  const card = (heading) => scanCard({ jobId: "job-1", overview: { headline: "Quiz 2", state: "done", stages: graded }, escape, heading });
  assert.match(card(true), /<a class="button" data-scan-next-action href="#grading\/plan-1">Open grading →<\/a>/);
  assert.match(card(false), /<a class="button primary" data-scan-next-action href="#grading\/plan-1">Open grading →<\/a>/);
  // A stage the worker has ('View progress') is quiet on the list too; one waiting for the instructor stays ivory.
  const working = stages.map((item) => item.key === "students" ? { ...item, status: "Working", urgent: false } : item);
  assert.match(scanCard({ jobId: "job-1", overview: { headline: "h", state: "wait", stages: working }, escape }), /<a class="button" data-scan-next-action href="#inbox\/job\/job-1\/students">View progress →<\/a>/);
  assert.match(scanCard({ jobId: "job-1", overview: { headline: "h", state: "todo", stages }, escape }), /<a class="button primary" data-scan-next-action/);
});
